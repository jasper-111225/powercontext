from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import DBUnavailable
from app.main import create_app

CHUNK = {
    "id": 42,
    "doc_id": 1,
    "doc_title": "获取代码、编译并运行 seekdb",
    "source": "build-and-run.md",
    "heading": "编译生产二进制",
    "page": None,
    "text": "编译产物位于： build_release/src/observer/seekdb",
    "vec_sim": 0.82,
    "ft_score": 1.5,
    "vec_rank": 2,
    "ft_rank": 1,
    "rrf": 0.032,
}

DOCUMENT = {
    "id": 1,
    "source": "build-and-run.md",
    "title": "获取代码、编译并运行 seekdb",
    "kind": "markdown",
    "chars": 4200,
    "chunks": 6,
    "created_at": "2026-10-28 10:00:00",
}

STATS = {"documents": 1, "chunks": 1, "chars": 4200, "avg_chunk_chars": 420.0}


class FakeDB:
    def __init__(self, *, fail: bool = False, contains: bool = True) -> None:
        self.fail = fail
        self.contains = contains
        self.logged: list[dict] = []
        self.notes_written: list[dict] = []

    def _guard(self) -> None:
        if self.fail:
            raise DBUnavailable("seekdb 连接失败")

    def ping(self):
        if self.fail:
            return False, "seekdb 连接失败"
        return True, "ok"

    def stats(self):
        self._guard()
        return dict(STATS)

    def list_documents(self):
        self._guard()
        return [dict(DOCUMENT)]

    def history(self, limit: int = 20):
        self._guard()
        return [{"question": "Release 模式的编译产物在哪个目录？", "mode": "llm"}][:limit]

    def hybrid_search(self, question, vector, limit=None, doc_id=None):
        self._guard()
        row = dict(CHUNK)
        if doc_id is not None:
            row["doc_id"] = doc_id
        return {"fused": [row], "vector": [row], "fulltext": [row]}

    def corpus_contains(self, token: str) -> bool:
        return self.contains

    def log_query(self, **kwargs):
        self.logged.append(kwargs)

    def delete_document(self, doc_id: int) -> bool:
        self._guard()
        return doc_id == 1

    def add_note(self, *, user_id, question, answer, citations):
        self.notes_written.append({"user_id": user_id, "citations": citations})
        return {"id": 7, "question": question, "answer": answer, "citations": citations}

    def notes(self, limit: int = 50, user_id=None):
        self._guard()
        return []


class FakeEmbedder:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def embed_query(self, text: str):
        from app.embed import EmbeddingError

        if self.fail:
            raise EmbeddingError("向量模型不可用")
        return [0.1, 0.2, 0.3]

    def available(self):
        return (not self.fail), "ok"


class FakeLLM:
    def __init__(self, *, reply: str = "编译产物位于 build_release/src/observer/seekdb [1]。") -> None:
        self.reply = reply

    def generate(self, system: str, user: str) -> str:
        return self.reply

    def available(self):
        return True, "已安装 qwen2.5:0.5b"


class FakeMemory:
    def __init__(self) -> None:
        self.profile: dict = {}

    def health(self):
        return True, "ok"

    def snapshot(self):
        return {
            "connected": True,
            "source": "powercontext",
            "scope_id": "scp_test",
            "entries": len(self.profile),
            "profile": dict(self.profile),
            "progress": {},
        }

    def save_profile(self, topic: str, style: str, goal: str):
        self.profile = {"topic": topic, "style": style, "goal": goal}
        return {"source": "powercontext", "profile": dict(self.profile)}

    def record_progress(self, **kwargs):
        return {"source": "powercontext"}


class FakePipeline:
    def ingest_upload(self, filename: str, data: bytes):
        class Report:
            def to_dict(self):
                return {
                    "doc_id": 9,
                    "title": "实验笔记",
                    "source": "uploads/实验笔记.md",
                    "kind": "markdown",
                    "chars": 463,
                    "chunks": 2,
                }

        return Report()


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    settings = Settings(root=tmp_path, llm_enabled=True)
    app = create_app(
        settings,
        db=FakeDB(),
        embedder=FakeEmbedder(),
        llm=FakeLLM(),
        memory=FakeMemory(),
        pipeline=FakePipeline(),
    )
    return TestClient(app)


def test_health_reports_components(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["db"] == "ok"
    assert body["corpus"]["documents"] == 1
    assert body["vector"] is True and body["llm"] is True
    assert body["memory"] == "ok"


def test_ask_returns_citation_tied_to_chunk(client: TestClient) -> None:
    response = client.post("/api/ask", json={"question": "Release 模式的编译产物在哪个目录？"})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "llm"
    assert body["citations"][0]["chunk_id"] == 42
    assert body["citations"][0]["n"] == 1
    assert body["evidence"][0]["used"] is True
    assert body["trace"]["vector"][0]["chunk_id"] == 42
    assert body["embedding"]["used"] is True


def test_ask_rejects_out_of_corpus_entity(tmp_path: Path) -> None:
    settings = Settings(root=tmp_path, llm_enabled=True)
    app = create_app(
        settings,
        db=FakeDB(contains=False),
        embedder=FakeEmbedder(),
        llm=FakeLLM(),
        memory=FakeMemory(),
    )
    body = TestClient(app).post("/api/ask", json={"question": "seekdb 支持 MongoDB 协议吗？"}).json()
    assert body["mode"] == "insufficient"
    assert body["citations"] == []
    assert "mongodb" in body["reason"]


def test_ask_falls_back_when_embedding_fails(tmp_path: Path) -> None:
    settings = Settings(root=tmp_path, llm_enabled=True)
    app = create_app(
        settings,
        db=FakeDB(),
        embedder=FakeEmbedder(fail=True),
        llm=FakeLLM(),
        memory=FakeMemory(),
    )
    body = TestClient(app).post("/api/ask", json={"question": "编译产物在哪个目录？"}).json()
    assert body["embedding"]["used"] is False
    assert body["embedding"]["error"]
    assert body["answer"]


def test_ask_503_when_db_down(tmp_path: Path) -> None:
    settings = Settings(root=tmp_path, llm_enabled=True)
    app = create_app(
        settings,
        db=FakeDB(fail=True),
        embedder=FakeEmbedder(),
        llm=FakeLLM(),
        memory=FakeMemory(),
    )
    response = TestClient(app).post("/api/ask", json={"question": "编译产物在哪？"})
    assert response.status_code == 503
    assert "seekdb" in response.json()["detail"]


def test_bootstrap_lists_documents_and_suggestions(client: TestClient) -> None:
    body = client.get("/api/bootstrap").json()
    assert body["db"] == "ok"
    assert body["documents"][0]["source"] == "build-and-run.md"
    assert body["stats"]["chunks"] == 1
    assert body["suggestions"]
    assert body["memory"]["profile"] == {}


def test_memory_profile_roundtrip(client: TestClient) -> None:
    response = client.post(
        "/api/memory/profile",
        json={"topic": "seekdb 内核开发", "style": "concise", "goal": "跑通一次编译"},
    )
    assert response.status_code == 200
    assert response.json()["profile"]["topic"] == "seekdb 内核开发"
    snapshot = client.get("/api/memory").json()
    assert snapshot["profile"]["goal"] == "跑通一次编译"


def test_note_keeps_only_citations_with_chunk_id(client: TestClient) -> None:
    response = client.post(
        "/api/notes",
        json={
            "question": "编译产物在哪？",
            "answer": "在 build_release 下 [1]",
            "citations": [{"n": 1, "chunk_id": 42}, {"n": 2}, {"text": "无编号"}],
        },
    )
    assert response.status_code == 200
    assert response.json()["note"]["id"] == 7


def test_empty_question_is_rejected(client: TestClient) -> None:
    assert client.post("/api/ask", json={"question": ""}).status_code == 422


def test_eval_absent_is_reported_not_faked(client: TestClient) -> None:
    body = client.get("/api/eval").json()
    assert body["available"] is False
    assert "scripts/eval.py" in body["message"]


def test_import_returns_ingest_report(client: TestClient) -> None:
    response = client.post(
        "/api/import",
        files={"file": ("实验笔记.md", b"# note\n\nhello", "text/markdown")},
    )
    assert response.status_code == 200
    assert response.json()["chunks"] == 2


def test_import_rejects_oversized_file(tmp_path: Path) -> None:
    settings = Settings(root=tmp_path, max_upload_mb=1)
    app = create_app(
        settings,
        db=FakeDB(),
        embedder=FakeEmbedder(),
        llm=FakeLLM(),
        memory=FakeMemory(),
        pipeline=FakePipeline(),
    )
    response = TestClient(app).post(
        "/api/import",
        files={"file": ("big.md", b"x" * (1024 * 1024 + 1), "text/markdown")},
    )
    assert response.status_code == 413
