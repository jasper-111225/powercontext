from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.embed import Embedder
from app.memory import Memory, MemoryError


def _offline_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_memory_falls_back_to_local_json(tmp_path):
    settings = Settings(root=tmp_path, memory_timeout=0.5)
    memory = Memory(settings, client=_offline_client())

    snapshot = memory.snapshot()
    assert snapshot["source"] == "local"
    assert snapshot["profile"] is None

    saved = memory.save_profile("seekdb 内核开发", "concise", "读完内存文档")
    assert saved["source"] == "local"
    assert settings.memory_fallback_path.exists()

    again = memory.snapshot()
    assert again["source"] == "local"
    assert again["profile"]["topic"] == "seekdb 内核开发"
    assert again["profile"]["goal"] == "读完内存文档"

    progress = memory.record_progress(
        question="怎么用 GDB 调试 seekdb？",
        confidence=0.7,
        mode="extractive",
        docs=["调试 seekdb"],
        insufficient=False,
    )
    assert progress["source"] == "local"
    assert progress["progress"]["asked"] == 1
    assert memory.snapshot()["progress"]["last_question"] == "怎么用 GDB 调试 seekdb？"

    weak = memory.record_progress(
        question="seekdb 支持 MongoDB 协议吗？",
        confidence=0.1,
        mode="insufficient",
        docs=[],
        insufficient=True,
    )
    assert weak["progress"]["asked"] == 2
    assert "seekdb 支持 MongoDB 协议吗？" in weak["progress"]["weak_topics"]


def _record(artifact_id: str, kind: str, text: str) -> dict:
    return {
        "artifact": {"family": "atomic-memory", "artifact_id": artifact_id, "revision": 1},
        "kind": kind,
        "text": text,
        "state": "active",
        "state_version": 1,
        "merged_into_id": None,
    }


def test_memory_remote_scope_and_entries(tmp_path):
    calls: list[str] = []
    list_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        body = json.loads(request.content) if request.content else {}
        if path == "/v1/scopes":
            assert body["title"] == "judian-learning"
            assert body["summary"] and body["idempotency_key"]
            return httpx.Response(
                201,
                json={
                    "scope_id": "scp_test",
                    "title": "judian-learning",
                    "summary": "据典",
                    "context_references": [],
                    "external_references": [],
                    "version": 1,
                },
            )
        if path == "/v1/memory/remember":
            assert set(body) == {"scope_id", "kind", "text"}
            assert body["scope_id"] == "scp_test"
            json.loads(body["text"])
            return httpx.Response(
                200,
                json={
                    "changed": True,
                    "records": [
                        _record("am_new", body["kind"], body["text"]),
                    ],
                },
            )
        if path == "/v1/memory/entries/list":
            list_bodies.append(body)
            assert body["scope_id"] == "scp_test"
            assert "kind" not in body
            assert 1 <= body["limit"] <= 100
            if "cursor" not in body:
                return httpx.Response(
                    200,
                    json={
                        "entries": [
                            _record(
                                "am_01",
                                "judian-profile",
                                json.dumps({"topic": "旧主题"}, ensure_ascii=False),
                            ),
                            _record(
                                "am_02",
                                "judian-progress",
                                json.dumps({"asked": 1}, ensure_ascii=False),
                            ),
                        ],
                        "next_cursor": "page-2",
                    },
                )
            assert body["cursor"] == "page-2"
            return httpx.Response(
                200,
                json={
                    "entries": [
                        _record(
                            "am_03",
                            "judian-profile",
                            json.dumps(
                                {"topic": "seekdb 内核开发", "style": "concise"},
                                ensure_ascii=False,
                            ),
                        ),
                        _record(
                            "am_04",
                            "judian-progress",
                            json.dumps({"asked": 3}, ensure_ascii=False),
                        ),
                    ],
                    "next_cursor": None,
                },
            )
        return httpx.Response(404)

    settings = Settings(root=tmp_path, memory_timeout=1)
    memory = Memory(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    snapshot = memory.snapshot()
    assert snapshot["source"] == "powercontext"
    assert snapshot["scope_id"] == "scp_test"
    assert snapshot["entries"] == 4
    assert snapshot["profile"]["topic"] == "seekdb 内核开发"
    assert snapshot["progress"]["asked"] == 3
    assert snapshot["recent"][0] == {"asked": 3}
    assert [body.get("cursor") for body in list_bodies] == [None, "page-2"]
    assert json.loads(settings.scope_cache_path.read_text(encoding="utf-8"))["scope_id"] == "scp_test"

    result = memory.save_profile("新主题", "detailed", "目标")
    assert result["source"] == "powercontext"
    assert calls.count("/v1/scopes") == 1
    assert "/v1/memory/remember" in calls


def test_memory_picks_latest_entry_by_timestamp(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/scopes":
            return httpx.Response(201, json={"scope_id": "scp_test"})
        if request.url.path == "/v1/memory/entries/list":
            return httpx.Response(
                200,
                json={
                    "entries": [
                        _record(
                            "am_p2",
                            "judian-progress",
                            json.dumps({"asked": 5, "updated_at": "2026-10-28T12:00:00+00:00"}),
                        ),
                        _record(
                            "am_new",
                            "judian-profile",
                            json.dumps(
                                {"topic": "新主题", "saved_at": "2026-10-28T13:15:00+00:00"},
                                ensure_ascii=False,
                            ),
                        ),
                        _record(
                            "am_old",
                            "judian-profile",
                            json.dumps(
                                {"topic": "旧主题", "saved_at": "2026-10-28T11:00:00+00:00"},
                                ensure_ascii=False,
                            ),
                        ),
                        _record(
                            "am_p1",
                            "judian-progress",
                            json.dumps({"asked": 1, "updated_at": "2026-10-28T10:00:00+00:00"}),
                        ),
                    ],
                    "next_cursor": None,
                },
            )
        return httpx.Response(404)

    memory = Memory(
        Settings(root=tmp_path), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    snapshot = memory.snapshot()
    assert snapshot["profile"]["topic"] == "新主题"
    assert snapshot["progress"]["asked"] == 5
    assert snapshot["recent"][0]["topic"] == "新主题"
    assert snapshot["recent"][1]["asked"] == 5


def test_memory_health_uses_readiness_probe(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ready"})
        return httpx.Response(404)

    memory = Memory(
        Settings(root=tmp_path), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    ok, detail = memory.health()
    assert ok
    assert "/health/ready" in detail


def test_memory_rejects_payload_over_8kb(tmp_path):
    settings = Settings(root=tmp_path)
    memory = Memory(settings, client=_offline_client())
    with pytest.raises(MemoryError):
        memory.remember("judian-profile", {"text": "x" * 9000})


def test_embedder_uses_disk_cache_and_batches(tmp_path):
    calls: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        calls.append(list(payload["input"]))
        data = [
            {"index": index, "embedding": [0.1, 0.2, 0.3]}
            for index, _ in enumerate(payload["input"])
        ]
        return httpx.Response(200, json={"data": data})

    settings = Settings(root=tmp_path, embed_dim=3, embed_batch=2)
    embedder = Embedder(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    first = embedder.embed_texts(["a", "b", "c"])
    assert len(first) == 3
    assert first[0] == [0.1, 0.2, 0.3]
    assert [len(batch) for batch in calls] == [2, 1]
    assert settings.embed_cache_path.exists()

    second = embedder.embed_texts(["a", "b", "c"])
    assert second == first
    assert len(calls) == 2
