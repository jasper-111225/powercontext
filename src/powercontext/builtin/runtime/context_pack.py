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

"""Chunked Context Packs and prefix-reuse deltas for incremental transport."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512
_DIGEST_PREFIX = "sha256:"
_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})


class ContextPackError(ValueError):
    """Raised when a Context Pack or delta violates the wire contract."""


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(values: Any) -> tuple[bytes, ...]:
    if not isinstance(values, list):
        raise ContextPackError("items must be a list of Base64 strings")  # noqa: TRY003
    decoded: list[bytes] = []
    for value in values:
        if not isinstance(value, str):
            raise ContextPackError("items must be a list of Base64 strings")  # noqa: TRY003
        try:
            raw = base64.b64decode(value.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise ContextPackError("item is not valid Base64") from exc  # noqa: TRY003
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContextPackError("item is not valid UTF-8") from exc  # noqa: TRY003
        decoded.append(raw)
    return tuple(decoded)


def _pack_digest(items: tuple[bytes, ...]) -> str:
    return (
        _DIGEST_PREFIX + hashlib.sha256(_canonical({"schema": PACK_SCHEMA, "items": _encode_items(items)})).hexdigest()
    )


def _require_digest(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.startswith(_DIGEST_PREFIX):
        raise ContextPackError(f"{name} must be a sha256 digest")  # noqa: TRY003
    hex_part = value[len(_DIGEST_PREFIX) :]
    if len(hex_part) != 64 or any(character not in "0123456789abcdef" for character in hex_part):
        raise ContextPackError(f"{name} must be a sha256 digest")  # noqa: TRY003
    return value


@dataclass(frozen=True)
class ContextPack:
    """One complete context body split into transport chunks."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        item_tuple = tuple(items)
        digest = _pack_digest(item_tuple)
        payload: dict[str, Any] = {
            "schema": PACK_SCHEMA,
            "items": _encode_items(item_tuple),
            "digest": digest,
            "byte_count": 0,
        }
        size = 0
        while True:
            payload["byte_count"] = size
            actual = len(_canonical(payload))
            if actual == size:
                break
            size = actual
        return cls(items=item_tuple, digest=digest, byte_count=size)

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        if chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be positive")  # noqa: TRY003
        chunks: list[bytes] = []
        current: list[str] = []
        current_bytes = 0
        for character in content:
            encoded = character.encode("utf-8")
            if len(encoded) > chunk_bytes:
                raise ContextPackError("chunk_bytes cannot hold one character")  # noqa: TRY003
            if current_bytes + len(encoded) > chunk_bytes:
                chunks.append("".join(current).encode("utf-8"))
                current = []
                current_bytes = 0
            current.append(character)
            current_bytes += len(encoded)
        if current:
            chunks.append("".join(current).encode("utf-8"))
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        return _canonical({
            "schema": PACK_SCHEMA,
            "items": _encode_items(self.items),
            "digest": self.digest,
            "byte_count": self.byte_count,
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        payload = _load_object(data, _PACK_FIELDS)
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("unexpected Context Pack schema")  # noqa: TRY003
        items = _decode_items(payload["items"])
        digest = _require_digest("digest", payload["digest"])
        if digest != _pack_digest(items):
            raise ContextPackError("Context Pack digest does not match its items")  # noqa: TRY003
        byte_count = payload["byte_count"]
        if type(byte_count) is not int or byte_count != len(data):
            raise ContextPackError("Context Pack byte_count does not match its wire size")  # noqa: TRY003
        return cls(items=items, digest=digest, byte_count=byte_count)


@dataclass(frozen=True)
class ContextPackDelta:
    """A reusable base prefix plus the target suffix needed to rebuild one Pack."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        prefix = 0
        for left, right in zip(base.items, target.items, strict=False):
            if left != right:
                break
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=target.items[prefix:],
        )

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _canonical({
            "schema": DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode_items(self.appended),
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        payload = _load_object(data, _DELTA_FIELDS)
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("unexpected Context Pack delta schema")  # noqa: TRY003
        base_digest = _require_digest("base_digest", payload["base_digest"])
        target_digest = _require_digest("target_digest", payload["target_digest"])
        prefix_items = payload["prefix_items"]
        if type(prefix_items) is not int or prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")  # noqa: TRY003
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix_items,
            appended=_decode_items(payload["appended"]),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Validate one delta against its base and return the rebuilt target Pack."""

    if base.digest != delta.base_digest:
        raise ContextPackError("delta base digest does not match the supplied Pack")  # noqa: TRY003
    if delta.prefix_items < 0 or delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix_items is out of range")  # noqa: TRY003
    rebuilt = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if rebuilt.digest != delta.target_digest:
        raise ContextPackError("delta target digest does not match the rebuilt Pack")  # noqa: TRY003
    return rebuilt


def _load_object(data: bytes, fields: frozenset[str]) -> dict[str, Any]:
    try:
        payload = json.loads(data)
    except (TypeError, ValueError) as exc:
        raise ContextPackError("payload is not valid JSON") from exc  # noqa: TRY003
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ContextPackError("payload fields do not match the wire contract")  # noqa: TRY003
    return payload


__all__ = [
    "DEFAULT_CHUNK_BYTES",
    "DELTA_SCHEMA",
    "PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
