"""Thin HTTP client for a local PowerContext Server (Memory API).

Task progress is stored as an explicit Memory entry (``kind=sdd-task-state``) and
read back by a later process, which is how the module reuses state after a
restart. The server keeps the data in its own database, so the module itself is
stateless.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import Settings, load_settings

STATE_KIND = "sdd-task-state"
MAX_MEMORY_BYTES = 8000


class PowerContextError(RuntimeError):
    pass


class PowerContext:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.base = self.settings.powercontext_url.rstrip("/")
        self.token = self.settings.powercontext_token
        self.scope_file: Path = self.settings.scope_file

    # -- transport ------------------------------------------------------
    def _request(self, path: str, payload: dict[str, Any] | None = None,
                 method: str = "POST") -> dict[str, Any]:
        url = f"{self.base}{path}"
        data = None
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise PowerContextError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise PowerContextError(
                f"cannot reach PowerContext Server at {self.base}: {exc.reason}\n"
                "start it with scripts/pc_server.sh start"
            ) from exc
        return json.loads(body) if body else {}

    def health(self) -> bool:
        try:
            self._request("/health/ready", method="GET")
            return True
        except PowerContextError:
            return False

    # -- scopes ---------------------------------------------------------
    def ensure_scope(self, project: str) -> str:
        if self.scope_file.exists():
            scopes = json.loads(self.scope_file.read_text(encoding="utf-8")).get("scopes", {})
            if project in scopes:
                return scopes[project]

        payload = {
            "title": f"minimax-sdd-{project}",
            "summary": f"SDD context module scope for project {project}",
            "idempotency_key": f"minimax-sdd-scope-{project}",
        }
        try:
            created = self._request("/v1/scopes", payload)
            scope_id = created["scope_id"]
        except PowerContextError:
            listing = self._request("/v1/scopes", method="GET")
            scope_id = ""
            for item in listing.get("scopes", listing.get("items", [])):
                if item.get("title") == payload["title"]:
                    scope_id = item["scope_id"]
                    break
            if not scope_id:
                raise
        self.scope_file.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if self.scope_file.exists():
            existing = json.loads(self.scope_file.read_text(encoding="utf-8"))
        existing.setdefault("scopes", {})[project] = scope_id
        self.scope_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
        return scope_id

    # -- task state -----------------------------------------------------
    def save_state(self, project: str, state: dict[str, Any]) -> dict[str, Any]:
        scope_id = self.ensure_scope(project)
        text = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        if len(text.encode("utf-8")) > MAX_MEMORY_BYTES:
            raise PowerContextError(
                f"task state is {len(text.encode('utf-8'))} bytes; keep it under {MAX_MEMORY_BYTES}"
            )
        response = self._request(
            "/v1/memory/remember",
            {
                "scope_id": scope_id,
                "kind": STATE_KIND,
                "text": text,
                "reason": f"SDD task state for {project} ({state.get('stage')})",
            },
        )
        state = dict(state)
        state["scope_id"] = scope_id
        state["memory"] = response.get("records", [{}])[0].get("artifact", {})
        return state

    def load_states(self, project: str) -> list[dict[str, Any]]:
        scope_id = self.ensure_scope(project)
        response = self._request("/v1/memory/entries/list", {"scope_id": scope_id})
        states: list[dict[str, Any]] = []
        for entry in response.get("entries", []):
            if entry.get("kind") != STATE_KIND:
                continue
            try:
                state = json.loads(entry["text"])
            except json.JSONDecodeError:
                continue
            state["scope_id"] = scope_id
            state["state_entry"] = entry.get("artifact", {})
            states.append(state)
        states.sort(key=lambda item: item.get("saved_at", ""))
        return states

    def latest_state(self, project: str, task_id: str | None = None) -> dict[str, Any] | None:
        states = self.load_states(project)
        if task_id:
            states = [state for state in states if state.get("task_id") == task_id]
        return states[-1] if states else None
