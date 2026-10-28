"""PowerContext 记忆层。

「据典」把两类长期信息交给 PowerContext：
  * `judian-profile`  —— 你在研究什么、偏好什么回答风格；
  * `judian-progress` —— 上次问到哪、哪些主题没答好、累计问了几次。

记忆只在**显式**时机写入（设置偏好、完成一次问答），读取发生在每次打开页面
和每次提问前。Server 重启后这些信息仍在，这是本项目「越用越懂你」的证据。

PowerContext 不可用时自动退回本地 JSON，并在 API 响应里标注 `source`，
不让记忆故障拖垮主流程。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import RUNTIME_DIR, settings

PROFILE_KIND = "judian-profile"
PROGRESS_KIND = "judian-progress"
FALLBACK_FILE = RUNTIME_DIR / "memory_fallback.json"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class MemoryError(RuntimeError):
    pass

def _latest_of(entries: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    """按 saved_at 取最新一条；不依赖 entries 的返回顺序。"""
    best: dict[str, Any] | None = None
    for entry in entries:
        if entry.get("kind") != kind:
            continue
        try:
            payload = json.loads(entry.get("text") or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and (
            best is None or str(payload.get("saved_at", "")) >= str(best.get("saved_at", ""))
        ):
            best = payload
    return best





class Memory:
    def __init__(self, s=settings) -> None:
        self.s = s
        self.base = s.pc_url.rstrip("/")
        self._scope_id: str | None = None
        self.last_error = ""

    # ------------------------------------------------------------- transport
    def _request(self, path: str, payload: dict[str, Any] | None = None, method: str = "POST") -> dict:
        headers = {"Authorization": f"Bearer {self.s.pc_token}", "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        with httpx.Client(timeout=15.0) as client:
            response = client.request(method, f"{self.base}{path}", content=data, headers=headers)
            response.raise_for_status()
            body = response.text
        return json.loads(body) if body else {}

    def health(self) -> bool:
        try:
            self._request("/health/ready", method="GET")
            self.last_error = ""
            return True
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    # ----------------------------------------------------------------- scope
    def ensure_scope(self) -> str:
        if self._scope_id:
            return self._scope_id
        cache: Path = self.s.pc_scope_file
        if cache.exists():
            try:
                stored = json.loads(cache.read_text(encoding="utf-8"))
                if stored.get("scope_id"):
                    self._scope_id = stored["scope_id"]
                    return self._scope_id
            except Exception:  # noqa: BLE001
                pass
        payload = {
            "title": self.s.pc_scope_title,
            "summary": "据典 · 个人资料问答的学习主题、偏好与进度",
            "idempotency_key": f"judian-scope-{self.s.pc_scope_title}",
        }
        try:
            created = self._request("/v1/scopes", payload)
            scope_id = created.get("scope_id") or created.get("id")
        except Exception:
            listing = self._request("/v1/scopes", method="GET")
            scope_id = ""
            for item in listing.get("scopes", listing.get("items", [])):
                if item.get("title") == self.s.pc_scope_title:
                    scope_id = item.get("scope_id") or item.get("id")
                    break
        if not scope_id:
            raise MemoryError("无法创建或找到 PowerContext Scope")
        self._scope_id = str(scope_id)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"scope_id": scope_id}, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._scope_id

    # ---------------------------------------------------------------- entries
    def _entries(self) -> list[dict[str, Any]]:
        scope_id = self.ensure_scope()
        response = self._request("/v1/memory/entries/list", {"scope_id": scope_id, "limit": 100})
        return list(response.get("entries", []))

    def remember(self, kind: str, payload: dict[str, Any], reason: str) -> dict[str, Any]:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(text.encode("utf-8")) > 8000:
            raise MemoryError("记忆内容超过 PowerContext 单条 8KB 限制")
        scope_id = self.ensure_scope()
        response = self._request(
            "/v1/memory/remember",
            {"scope_id": scope_id, "kind": kind, "text": text, "reason": reason[:512]},
        )
        records = response.get("records", [])
        return records[0].get("artifact", {}) if records else {}

    def latest(self, kind: str) -> dict[str, Any] | None:
        return _latest_of(self._entries(), kind)

    # ------------------------------------------------------------------- api
    def _local_load(self) -> dict[str, Any]:
        if not FALLBACK_FILE.exists():
            return {}
        try:
            return json.loads(FALLBACK_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _local_save(self, kind: str, payload: dict[str, Any]) -> None:
        data = self._local_load()
        data.setdefault(kind, []).append({**payload, "saved_at": _now()})
        data[kind] = data[kind][-50:]
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        FALLBACK_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def save(self, kind: str, payload: dict[str, Any], reason: str) -> dict[str, Any]:
        payload = {**payload, "saved_at": _now()}
        try:
            artifact = self.remember(kind, payload, reason)
            return {"source": "powercontext", "artifact": artifact, "saved_at": payload["saved_at"]}
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._local_save(kind, payload)
            return {"source": "local", "error": self.last_error, "saved_at": payload["saved_at"]}

    def snapshot(self) -> dict[str, Any]:
        ok = self.health()
        profile: dict[str, Any] = {}
        progress: dict[str, Any] = {}
        count = 0
        recent: list[dict[str, Any]] = []
        source = "powercontext"
        error = self.last_error
        if ok:
            try:
                entries = self._entries()
                count = len(entries)
                recent = []
                for entry in entries[-6:]:
                    raw = entry.get("text") or ""
                    try:
                        payload = json.loads(raw)
                        label = (
                            payload.get("topic")
                            or payload.get("last_question")
                            or payload.get("goal")
                            or raw
                        )
                    except (json.JSONDecodeError, AttributeError):
                        label = raw
                    recent.append({"kind": entry.get("kind", ""), "text": str(label)[:160]})
                for kind, target in ((PROFILE_KIND, profile), (PROGRESS_KIND, progress)):
                    target.update(_latest_of(entries, kind) or {})
            except Exception as exc:  # noqa: BLE001
                ok = False
                error = f"{type(exc).__name__}: {exc}"
        if not ok:
            source = "local"
            local = self._local_load()
            for kind, target in ((PROFILE_KIND, profile), (PROGRESS_KIND, progress)):
                rows = local.get(kind) or []
                if rows:
                    target.update(rows[-1])
            count = sum(len(v) for v in local.values())
            recent = [
                {"kind": kind, "text": json.dumps(rows[-1], ensure_ascii=False)[:160]}
                for kind, rows in local.items()
                if rows
            ][-6:]
        progress.setdefault("asked", 0)
        progress["weak_topics"] = list(dict.fromkeys(progress.get("weak_topics") or []))[-5:]
        return {
            "connected": ok,
            "source": source,
            "error": error,
            "scope_id": self._scope_id,
            "entries": count,
            "profile": profile,
            "progress": progress,
            "recent": recent,
            "kinds": {"profile": PROFILE_KIND, "progress": PROGRESS_KIND},
        }

    def save_profile(self, *, topic: str, style: str, goal: str = "") -> dict[str, Any]:
        profile = {
            "topic": topic.strip()[:200],
            "style": "detailed" if style == "detailed" else "concise",
            "goal": goal.strip()[:300],
        }
        return self.save(PROFILE_KIND, profile, f"用户设置研究主题：{profile['topic'] or '未填写'}")

    def record_ask(
        self,
        *,
        question: str,
        confidence: float,
        answer_mode: str,
        cited_docs: list[str],
        weak_topic: bool,
    ) -> dict[str, Any]:
        snapshot = self.snapshot()
        progress = dict(snapshot.get("progress") or {})
        weak = [str(t) for t in progress.get("weak_topics", []) if t][-4:]
        if weak_topic and question[:80] not in weak:
            weak.append(question[:80])
        weak = list(dict.fromkeys(weak))[-5:]
        progress.update(
            {
                "asked": int(progress.get("asked", 0)) + 1,
                "last_question": question[:300],
                "last_confidence": round(confidence, 3),
                "last_mode": answer_mode,
                "last_docs": cited_docs[:3],
                "weak_topics": weak,
                "updated_at": _now(),
            }
        )
        return self.save(PROGRESS_KIND, progress, "记录一次问答后的学习进度")
