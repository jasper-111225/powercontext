from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import Settings

MAX_TEXT_BYTES = 8192
LIST_PAGE_SIZE = 100
MAX_LIST_PAGES = 10


class MemoryError(RuntimeError):
    pass


def _now() -> str:
    # 微秒精度：verify 的「写入→恢复」可能落在同一秒，秒级会平局导致 _latest 取错条目
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _content_dict(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"text": content}
    return {"text": str(content)}


class Memory:
    """PowerContext 1.4 client with a local JSON fallback."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ http
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.powercontext_token:
            headers["Authorization"] = f"Bearer {self.settings.powercontext_token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = f"{self.settings.powercontext_url}{path}"
        try:
            if self._client is not None:
                response = self._client.request(
                    method,
                    url,
                    json=json_body,
                    headers=self._headers(),
                    timeout=self.settings.memory_timeout,
                )
            else:
                with httpx.Client(timeout=self.settings.memory_timeout) as client:
                    response = client.request(
                        method, url, json=json_body, headers=self._headers()
                    )
            return response
        except Exception as exc:
            raise MemoryError(f"PowerContext 不可达: {exc}") from exc

    def health(self) -> tuple[bool, str]:
        try:
            ready = self._request("GET", "/health/ready")
        except MemoryError as exc:
            return False, str(exc)
        if ready.status_code in (401, 403):
            return False, "鉴权失败（检查 JUDIAN_POWERCONTEXT_TOKEN）"
        if 200 <= ready.status_code < 300:
            return True, "HTTP 200 /health/ready"
        try:
            live = self._request("GET", "/health/live")
        except MemoryError as exc:
            return False, str(exc)
        if live.status_code in (401, 403):
            return False, "鉴权失败（检查 JUDIAN_POWERCONTEXT_TOKEN）"
        if 200 <= live.status_code < 300:
            return False, f"服务存活但未就绪: /health/ready HTTP {ready.status_code}"
        return False, f"健康检查失败: HTTP {live.status_code}"

    # ----------------------------------------------------------------- scope
    def _read_scope_cache(self) -> str | None:
        path = self.settings.scope_cache_path
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        scope_id = data.get("scope_id")
        return str(scope_id) if scope_id else None

    def _write_scope_cache(self, scope_id: str) -> None:
        path = self.settings.scope_cache_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"scope_id": scope_id, "title": self.settings.scope_title, "saved_at": _now()}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def _clear_scope_cache(self) -> None:
        try:
            self.settings.scope_cache_path.unlink()
        except OSError:
            pass

    def _create_scope(self) -> str:
        response = self._request(
            "POST",
            "/v1/scopes",
            json_body={
                "title": self.settings.scope_title,
                "summary": "据典 · 个人资料问答的学习主题、偏好与进度",
                "idempotency_key": "judian-learning-v1",
            },
        )
        if not 200 <= response.status_code < 300:
            raise MemoryError(f"创建 Scope 失败: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise MemoryError("创建 Scope 返回非 JSON") from exc
        scope = body.get("scope") if isinstance(body.get("scope"), dict) else body
        scope_id = scope.get("scope_id") or scope.get("id") or body.get("scope_id")
        if not scope_id:
            raise MemoryError("创建 Scope 未返回 scope_id")
        scope_id = str(scope_id)
        self._write_scope_cache(scope_id)
        return scope_id

    def scope_id(self) -> str:
        cached = self._read_scope_cache()
        if cached:
            return cached
        return self._create_scope()

    # --------------------------------------------------------------- remote
    def _parse_entry(self, raw: dict[str, Any], index: int) -> dict[str, Any]:
        artifact = raw.get("artifact")
        if not isinstance(artifact, dict):
            artifact = {}
        content = raw.get("content")
        if content is None:
            content = raw.get("text")
        if content is None:
            content = raw.get("memory") or {}
        entry_id = (
            artifact.get("artifact_id")
            or raw.get("id")
            or raw.get("entry_id")
            or f"remote-{index}"
        )
        return {
            "id": str(entry_id),
            "kind": str(raw.get("kind") or raw.get("type") or ""),
            "content": _content_dict(content),
            "created_at": raw.get("created_at") or raw.get("timestamp") or "",
        }

    def _remote_list(self) -> list[dict[str, Any]]:
        """Read every active entry; entries/list pages ascending by artifact id."""
        entries: list[dict[str, Any]] = []
        cursor: str | None = None
        scope_id = self.scope_id()
        recovered = False
        for _ in range(MAX_LIST_PAGES):
            payload: dict[str, Any] = {"scope_id": scope_id, "limit": LIST_PAGE_SIZE}
            if cursor:
                payload["cursor"] = cursor
            response = self._request(
                "POST", "/v1/memory/entries/list", json_body=payload
            )
            if response.status_code == 404 and not recovered:
                self._clear_scope_cache()
                scope_id = self.scope_id()
                cursor = None
                entries = []
                recovered = True
                continue
            if response.status_code >= 400:
                raise MemoryError(f"读取记忆失败: HTTP {response.status_code}")
            try:
                body = response.json()
            except ValueError as exc:
                raise MemoryError("读取记忆返回非 JSON") from exc
            if isinstance(body, list):
                raw_entries = body
                next_cursor = None
            elif isinstance(body, dict):
                raw_entries = body.get("entries") or []
                next_cursor = body.get("next_cursor")
            else:
                raise MemoryError("读取记忆返回格式不正确")
            for raw in raw_entries:
                if isinstance(raw, dict):
                    entries.append(self._parse_entry(raw, len(entries)))
            if not next_cursor:
                break
            cursor = str(next_cursor)
        return entries

    # --------------------------------------------------------------- fallback
    def _read_local(self) -> dict[str, Any]:
        path = self.settings.memory_fallback_path
        if not path.exists():
            return {"entries": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"entries": []}
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            return {"entries": []}
        return data

    def _write_local(self, data: dict[str, Any]) -> None:
        path = self.settings.memory_fallback_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _local_remember(self, kind: str, content: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read_local()
            entry = {
                "id": f"local-{uuid.uuid4().hex[:12]}",
                "kind": kind,
                "content": content,
                "created_at": _now(),
            }
            entries = data.get("entries", [])
            entries.append(entry)
            data["entries"] = entries[-200:]
            if kind == self.settings.profile_kind:
                data["profile"] = content
            elif kind == self.settings.progress_kind:
                data["progress"] = content
            self._write_local(data)
        return entry

    def _local_snapshot(self) -> dict[str, Any]:
        data = self._read_local()
        profile = None
        progress = None
        for entry in data.get("entries", []):
            if entry.get("kind") == self.settings.profile_kind:
                profile = entry.get("content")
            elif entry.get("kind") == self.settings.progress_kind:
                progress = entry.get("content")
        recent = [
            entry.get("content", {})
            for entry in list(reversed(data.get("entries", [])))[:5]
        ]
        return {
            "connected": False,
            "source": "local",
            "scope_id": self._read_scope_cache(),
            "entries": len(data.get("entries", [])),
            "profile": profile,
            "progress": progress,
            "recent": recent,
        }

    # ------------------------------------------------------------------ write
    def _remember_remote(
        self, scope_id: str, kind: str, text: str
    ) -> httpx.Response:
        return self._request(
            "POST",
            "/v1/memory/remember",
            json_body={"scope_id": scope_id, "kind": kind, "text": text},
        )

    def remember(
        self,
        kind: str,
        content: dict[str, Any],
        *,
        local_only: bool = False,
    ) -> dict[str, Any]:
        text = json.dumps(content, ensure_ascii=False)
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise MemoryError("单条记忆超过 8KB 限制")
        if local_only:
            entry = self._local_remember(kind, content)
            return {"source": "local", "entry": entry}
        try:
            scope_id = self.scope_id()
            response = self._remember_remote(scope_id, kind, text)
            if response.status_code == 404:
                self._clear_scope_cache()
                scope_id = self.scope_id()
                response = self._remember_remote(scope_id, kind, text)
            if response.status_code >= 400:
                raise MemoryError(f"写入记忆失败: HTTP {response.status_code}")
            try:
                body = response.json()
            except ValueError:
                body = {}
            records = body.get("records") if isinstance(body, dict) else None
            record = records[0] if isinstance(records, list) and records else {}
            artifact = record.get("artifact") if isinstance(record, dict) else None
            if not isinstance(artifact, dict):
                artifact = {}
            entry_id = str(
                artifact.get("artifact_id")
                or body.get("id")
                or body.get("entry_id")
                or ""
            )
            return {
                "source": "powercontext",
                "entry": {
                    "id": entry_id,
                    "kind": kind,
                    "content": content,
                    "created_at": _now(),
                },
            }
        except MemoryError:
            entry = self._local_remember(kind, content)
            return {"source": "local", "entry": entry}

    def save_profile(self, topic: str, style: str = "", goal: str = "") -> dict[str, Any]:
        profile = {
            "topic": topic.strip(),
            "style": style.strip(),
            "goal": goal.strip(),
            "saved_at": _now(),
        }
        result = self.remember(self.settings.profile_kind, profile)
        return {
            "source": result["source"],
            "profile": profile,
            "connected": result["source"] == "powercontext",
        }

    def record_progress(
        self,
        *,
        question: str,
        confidence: float,
        mode: str,
        docs: list[str],
        insufficient: bool,
    ) -> dict[str, Any]:
        snapshot = self.snapshot()
        progress = dict(snapshot.get("progress") or {})
        progress["asked"] = int(progress.get("asked") or 0) + 1
        progress["last_question"] = question
        progress["last_confidence"] = round(float(confidence), 4)
        progress["last_mode"] = mode
        progress["last_docs"] = list(dict.fromkeys(docs))[:5]
        weak_topics = list(progress.get("weak_topics") or [])
        if insufficient:
            weak_topics = list(dict.fromkeys([question, *weak_topics]))[:5]
        progress["weak_topics"] = weak_topics
        progress["updated_at"] = _now()
        result = self.remember(
            self.settings.progress_kind,
            progress,
            local_only=snapshot.get("source") == "local",
        )
        return {"source": result["source"], "progress": progress}

    # ------------------------------------------------------------------- read
    @staticmethod
    def _entry_stamp(entry: dict[str, Any]) -> str:
        content = entry.get("content")
        if not isinstance(content, dict):
            return ""
        return str(content.get("saved_at") or content.get("updated_at") or "")

    def _latest(self, entries: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
        picked: dict[str, Any] | None = None
        picked_stamp = ""
        for entry in entries:
            if entry.get("kind") != kind:
                continue
            stamp = self._entry_stamp(entry)
            content = entry.get("content")
            if picked is None or stamp >= picked_stamp:
                picked = content if isinstance(content, dict) else None
                picked_stamp = stamp
        return picked

    def snapshot(self) -> dict[str, Any]:
        try:
            entries = self._remote_list()
        except MemoryError as exc:
            snapshot = self._local_snapshot()
            snapshot["error"] = str(exc)
            return snapshot

        profile = self._latest(entries, self.settings.profile_kind)
        progress = self._latest(entries, self.settings.progress_kind)
        ordered = sorted(entries, key=self._entry_stamp)
        recent = [entry.get("content", {}) for entry in list(reversed(ordered))[:5]]
        return {
            "connected": True,
            "source": "powercontext",
            "scope_id": self._read_scope_cache(),
            "entries": len(entries),
            "profile": profile,
            "progress": progress,
            "recent": recent,
        }
