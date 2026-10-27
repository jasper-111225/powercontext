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

"""Capture one Pi session branch as immutable, resumable event evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    SourceDefinition,
    TextEvidence,
)
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_EVENT_SOURCE_VERSION = "1"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"


class PiSessionInput(BaseModel):
    """One UTF-8 JSONL Pi session file plus the branch leaf to materialize."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on the selected branch, with its full source record."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """Captured Pi event evidence bound to its exact session identity."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Derive one stable, bounded identity for a captured Pi event."""

    source_id = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        compact_session = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
        compact_event = hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:32]
        source_id = f"pi:{compact_session}:{compact_event}"
    return source_id


def _text_parts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return parts


def _tool_call_body(block: dict[str, Any]) -> str:
    arguments = block.get("arguments", {})
    encoded = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"[tool_call {block.get('name')} {block.get('id')}] {encoded}"


def _message_body(message: dict[str, Any]) -> str | None:
    role = message.get("role")
    if role == "toolResult":
        body = "\n".join(_text_parts(message.get("content")))
        return f"[tool_result {message.get('toolName')} {message.get('toolCallId')}] {body}"
    if role == "bashExecution":
        return f"$ {message.get('command', '')}\n{message.get('output', '')}"
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif block.get("type") == "toolCall":
            parts.append(_tool_call_body(block))
    return "\n".join(parts) if parts else None


def _event_body(record: dict[str, Any]) -> tuple[str, str] | None:
    event_type = record.get("type")
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        body = _message_body(message)
        if not isinstance(role, str) or not body:
            return None
        return role, body
    if event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        if not isinstance(summary, str) or not summary:
            return None
        return event_type, summary
    if event_type == "custom_message":
        body = "\n".join(_text_parts(record.get("content")))
        return ("custom", body) if body else None
    return None


def _records(content: bytes) -> list[dict[str, Any]]:
    try:
        text = content.decode("utf-8-sig")
    except (AttributeError, UnicodeDecodeError) as exc:
        raise ValueError("session file must be valid UTF-8") from exc  # noqa: TRY003
    parsed: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise ValueError("session file must be JSONL") from exc  # noqa: TRY003
        if not isinstance(value, dict):
            raise ValueError("session line must be a JSON object")  # noqa: TRY003, TRY004
        parsed.append(value)
    return parsed


def _session_header(records: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    if not records:
        raise ValueError("session file is empty")  # noqa: TRY003
    header = records[0]
    session_id = header.get("id")
    if header.get("type") != "session" or header.get("version") != 3:
        raise ValueError("session header must declare type=session and version=3")  # noqa: TRY003
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session header must carry a non-empty id")  # noqa: TRY003
    return session_id, records[1:]


def _session_nodes(body: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    nodes: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in body:
        node_id = record.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("every session record needs a non-empty id")  # noqa: TRY003
        if node_id in nodes:
            raise ValueError(f"duplicate session record id: {node_id!r}")  # noqa: TRY003
        parent_id = record.get("parentId")
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in nodes):
            raise ValueError(f"session record {node_id!r} references an unknown parent")  # noqa: TRY003
        nodes[node_id] = record
        order.append(node_id)
    return nodes, order


def _branch_chain(
    nodes: dict[str, dict[str, Any]],
    order: list[str],
    leaf_id: str | None,
) -> list[dict[str, Any]]:
    selected = leaf_id if leaf_id is not None else (order[-1] if order else None)
    if not isinstance(selected, str) or selected not in nodes:
        raise ValueError("selected leaf is not part of this session")  # noqa: TRY003
    chain: list[dict[str, Any]] = []
    cursor: str | None = selected
    while cursor is not None:
        record = nodes[cursor]
        chain.append(record)
        cursor = record.get("parentId")
    chain.reverse()
    return chain


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Return the visible events on the selected branch, ordered root to leaf."""

    session_id, body = _session_header(_records(value.content))
    nodes, order = _session_nodes(body)
    events: list[PiEventInput] = []
    for record in _branch_chain(nodes, order, value.leaf_id):
        visible = _event_body(record)
        if visible is None:
            continue
        role, content = visible
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=record["id"],
                parent_id=record.get("parentId"),
                event_type=record["type"],
                role=role,
                content=f"{role}: {content}",
                record=record,
            )
        )
    return tuple(events)


class PiEventSourceAdapter:
    """Resolve and read the immutable Pi event Source."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            definition_version=PI_EVENT_SOURCE_VERSION,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose one Pi event as canonical textual evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = AdapterSourceDefinition(
    PiEventSourceAdapter(),
    version=PI_EVENT_SOURCE_VERSION,
    projections=(PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Import the unseen visible events of one Pi branch, resuming from a checkpoint."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: Path | str, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        content = self._path.read_bytes()
        events = parse_pi_session(PiSessionInput(content=content, leaf_id=self._leaf_id))
        imported = _loaded_checkpoint(session.checkpoint)
        for event in events:
            digest = _event_digest(event)
            known = imported.get(event.event_id)
            if known is not None and known != digest:
                raise ValueError(f"imported Pi event {event.event_id!r} was rewritten")  # noqa: TRY003
            if known is None:
                await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
                imported[event.event_id] = digest
        checkpoint = {"events": [[event_id, digest] for event_id, digest in sorted(imported.items())]}
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=checkpoint)


def _event_digest(event: PiEventInput) -> str:
    payload = json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _loaded_checkpoint(checkpoint: JsonValue | None) -> dict[str, str]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("events"), list):
        raise ValueError("connector checkpoint is malformed")  # noqa: TRY003, TRY004
    imported: dict[str, str] = {}
    for entry in checkpoint["events"]:
        if not isinstance(entry, list) or len(entry) != 2 or not all(isinstance(part, str) for part in entry):
            raise ValueError("connector checkpoint is malformed")  # noqa: TRY003
        imported[entry[0]] = entry[1]
    return imported


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_SESSION_CONNECTOR_NAME",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
