# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Propagate Source availability through the derived-evidence dependency graph."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, insert, select

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_TABLE
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import SourceRef

LifecyclePurpose = Literal["generation", "audit"]
LifecyclePolicy = Literal["all", "any"]
LifecycleKind = Literal["source", "memory", "topic"]


class SourceLifecycleError(ValueError):
    """Report one invalid lifecycle registration, event, or resolution."""


class SourceLifecycleEvent(BaseModel):
    """One validated availability transition for a registered Source node."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    revision: int = Field(strict=True, gt=0)
    sequence: int = Field(strict=True, gt=0)
    available: bool = Field(strict=True)
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("lifecycle event text must be non-blank")  # noqa: TRY003
        return value


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    kind: LifecycleKind
    ref: dict[str, Any]
    parents: tuple[str, ...] = ()
    policy: LifecyclePolicy = "all"
    available: bool = True
    revision: int = 1
    sequence: int = 0


class _State(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[_Node] = []


def _require_node_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceLifecycleError("node_id must be a non-blank string")  # noqa: TRY003
    if value != value.strip():
        raise SourceLifecycleError("node_id must not carry surrounding whitespace")  # noqa: TRY003
    return value


def _require_policy(value: object) -> LifecyclePolicy:
    if value not in ("all", "any"):
        raise SourceLifecycleError("policy must be 'all' or 'any'")  # noqa: TRY003
    return value  # type: ignore[return-value]


class ScopedSourceLifecycle:
    """Register the dependency graph of one Scope and apply Source transitions."""

    def __init__(self, runtime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = scope_id

    # -- registration ----------------------------------------------------------------

    async def register_source(self, node_id: str, source_ref: SourceRef, /, *, revision: int) -> None:
        """Bind one captured Source to a lifecycle version."""

        node_id = _require_node_id(node_id)
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("revision must be a positive integer")  # noqa: TRY003
        ref = source_ref.model_dump(mode="json")
        state = await self._load()
        existing = self._find(state, node_id)
        if existing is not None:
            if existing.kind != "source" or existing.ref != ref or existing.revision != revision:
                raise SourceLifecycleError(f"conflicting registration for {node_id!r}")  # noqa: TRY003
            return
        await self._require_source(source_ref)
        state.nodes.append(
            _Node(node_id=node_id, kind="source", ref=ref, available=True, revision=revision, sequence=0)
        )
        await self._save(state)

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: tuple[str, ...],
        policy: LifecyclePolicy,
    ) -> None:
        """Bind one Memory entry to the Source dependencies that support it."""

        await self._register_derived("memory", node_id, citation.model_dump(mode="json"), parents, policy)
        entry = await self._call(self._memory_entry, citation)
        if entry is None:
            raise SourceLifecycleError("memory citation is not readable")  # noqa: TRY003

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        /,
        *,
        parents: tuple[str, ...],
        policy: LifecyclePolicy,
    ) -> None:
        """Bind one Topic Memory revision to the Source dependencies that support it."""

        await self._register_derived("topic", node_id, artifact_ref.model_dump(mode="json"), parents, policy)
        await self._require_artifact(artifact_ref)

    async def _register_derived(
        self,
        kind: Literal["memory", "topic"],
        node_id: str,
        ref: dict[str, Any],
        parents: tuple[str, ...],
        policy: LifecyclePolicy,
    ) -> None:
        node_id = _require_node_id(node_id)
        policy = _require_policy(policy)
        parent_ids = tuple(_require_node_id(parent) for parent in parents)
        if not parent_ids:
            raise SourceLifecycleError("a derived node needs at least one parent")  # noqa: TRY003
        if len(set(parent_ids)) != len(parent_ids):
            raise SourceLifecycleError("parents must be distinct")  # noqa: TRY003
        if node_id in parent_ids:
            raise SourceLifecycleError("a node cannot depend on itself")  # noqa: TRY003
        state = await self._load()
        existing = self._find(state, node_id)
        if existing is not None:
            if (
                existing.kind != kind
                or existing.ref != ref
                or existing.parents != parent_ids
                or existing.policy != policy
            ):
                raise SourceLifecycleError(f"conflicting registration for {node_id!r}")  # noqa: TRY003
            return
        await self._require_parents(state, parent_ids)
        available = _support(state, parent_ids, policy)
        state.nodes.append(
            _Node(
                node_id=node_id,
                kind=kind,
                ref=ref,
                parents=parent_ids,
                policy=policy,
                available=available,
            )
        )
        _recompute(state)
        await self._save(state)
        await self._sync_memory(state)

    # -- transitions -----------------------------------------------------------------

    async def apply(self, event: SourceLifecycleEvent, /) -> tuple[str, ...]:
        """Apply one Source event, propagate it, and return active node ids sorted."""

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("apply requires a SourceLifecycleEvent")  # noqa: TRY003
        state = await self._load()
        node = self._find(state, event.node_id)
        if node is None or node.kind != "source":
            raise SourceLifecycleError(f"unknown lifecycle Source {event.node_id!r}")  # noqa: TRY003
        if node.revision != event.revision:
            raise SourceLifecycleError("event revision does not match the registered Source")  # noqa: TRY003
        if event.sequence < node.sequence:
            raise SourceLifecycleError("event sequence regressed")  # noqa: TRY003
        if event.sequence == node.sequence:
            if node.available == event.available:
                return sorted(_active_ids(state))
            raise SourceLifecycleError("event sequence was reused with a different state")  # noqa: TRY003
        node.available = event.available
        node.sequence = event.sequence
        _recompute(state)
        await self._save(state)
        await self._sync_memory(state)
        return sorted(_active_ids(state))

    async def resolve(self, node_id: str, /, *, purpose: LifecyclePurpose) -> object:
        """Return the registered reference, rejecting unavailable generation reads."""

        node_id = _require_node_id(node_id)
        if purpose not in ("generation", "audit"):
            raise SourceLifecycleError("purpose must be 'generation' or 'audit'")  # noqa: TRY003
        state = await self._load()
        node = self._find(state, node_id)
        if node is None:
            raise SourceLifecycleError(f"unknown lifecycle node {node_id!r}")  # noqa: TRY003
        if purpose == "generation" and not node.available:
            raise SourceLifecycleError(f"lifecycle node {node_id!r} is unavailable")  # noqa: TRY003
        return _ref_object(node)

    async def inactive_topic_artifacts(self) -> frozenset[str]:
        """Return Topic artifact ids that must stay out of retrieval and context."""

        state = await self._load()
        return frozenset(node.ref["artifact_id"] for node in state.nodes if node.kind == "topic" and not node.available)

    # -- persistence -----------------------------------------------------------------

    async def _load(self) -> _State:
        database = getattr(self._runtime._provider, "database", None)
        if database is None:
            # Runtimes without a relational provider have no persisted lifecycle graph.
            return _State()
        async with database.transaction() as connection:
            payload = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_TABLE.c.payload).where(SOURCE_LIFECYCLE_TABLE.c.scope_id == self.scope_id)
                )
            ).scalar_one_or_none()
        if payload is None:
            return _State()
        return _State.model_validate(json.loads(bytes(payload)))

    async def _save(self, state: _State) -> None:
        database = getattr(self._runtime._provider, "database", None)
        if database is None:
            raise SourceLifecycleError("this Runtime has no persistent lifecycle store")  # noqa: TRY003
        payload = json.dumps(state.model_dump(mode="json"), separators=(",", ":"), sort_keys=True).encode("utf-8")
        async with database.transaction() as connection:
            await connection.execute(
                delete(SOURCE_LIFECYCLE_TABLE).where(SOURCE_LIFECYCLE_TABLE.c.scope_id == self.scope_id)
            )
            await connection.execute(insert(SOURCE_LIFECYCLE_TABLE).values(scope_id=self.scope_id, payload=payload))

    # -- helpers ---------------------------------------------------------------------

    @staticmethod
    def _find(state: _State, node_id: str) -> _Node | None:
        for node in state.nodes:
            if node.node_id == node_id:
                return node
        return None

    async def _require_parents(self, state: _State, parents: tuple[str, ...]) -> None:
        known = {node.node_id for node in state.nodes}
        for parent in parents:
            if parent not in known:
                raise SourceLifecycleError(f"unknown parent {parent!r}")  # noqa: TRY003

    async def _require_source(self, source_ref: SourceRef) -> None:
        async with self._runtime._context(self.scope_id) as context:
            entries = await context.sources.journal.entries()
        if not any(
            entry.source_ref.source_type == source_ref.source_type
            and entry.source_ref.source_id == source_ref.source_id
            for entry in entries
        ):
            raise SourceLifecycleError("registered Source does not exist in this Scope")  # noqa: TRY003

    async def _require_artifact(self, artifact_ref: ArtifactRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                artifact_ref.family, artifact_ref.artifact_id, artifact_ref.revision
            )
        except ArtifactNotFoundError as error:
            raise SourceLifecycleError("registered Topic revision does not exist") from error  # noqa: TRY003

    async def _memory_entry(self, citation: MemoryCitation) -> object | None:
        from powercontext.builtin.runtime.models import GetMemoryEntryRequest

        try:
            return await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=citation))
        except Exception:
            return None

    @staticmethod
    async def _call(function, argument):
        return await function(argument)

    async def _sync_memory(self, state: _State) -> None:
        memory_nodes = [node for node in state.nodes if node.kind == "memory"]
        if not memory_nodes:
            return
        wanted = {node.ref["entry_id"]: (node.ref["entry_version_id"], node.available) for node in memory_nodes}
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            service = context.artifacts.memory
            current = await self._head_or_none(service, context.artifacts.memory_artifact_id)
            if current is None:
                return
            for entry_id, (version_id, available) in wanted.items():
                manifest = {item.entry_id: item for item in current.content.manifest.entries}
                item = manifest.get(entry_id)
                if item is None:
                    continue
                desired = "active" if available else "inactive"
                if item.state == desired:
                    continue
                entries = tuple(
                    entry for entry in await service.entries(current) if entry.entry_version_id == version_id
                )
                if not entries:
                    continue
                if available:
                    current = await service.reactivate(current, entries=entries)
                else:
                    current = await service.forget(current, entries=entries)

    @staticmethod
    async def _head_or_none(service, artifact_id: str):
        try:
            return await service.head(artifact_id)
        except ArtifactNotFoundError:
            return None


class SourceLifecycleApplication:
    """Select the lifecycle service for one Scope."""

    def __init__(self, runtime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, scope_id)


def _support(state: _State, parents: tuple[str, ...], policy: LifecyclePolicy) -> bool:
    by_id = {node.node_id: node for node in state.nodes}
    values = [by_id[parent].available for parent in parents]
    return all(values) if policy == "all" else any(values)


def _recompute(state: _State) -> None:
    for node in state.nodes:
        if node.kind == "source":
            continue
        node.available = _support(state, node.parents, node.policy)


def _active_ids(state: _State) -> list[str]:
    return [node.node_id for node in state.nodes if node.available]


def _ref_object(node: _Node) -> object:
    if node.kind == "source":
        return SourceRef.model_validate(node.ref)
    if node.kind == "memory":
        return MemoryCitation.model_validate(node.ref)
    return ArtifactRef.model_validate(node.ref)


__all__ = [
    "ScopedSourceLifecycle",
    "SourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
]
