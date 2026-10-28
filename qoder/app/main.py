from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .db import DBUnavailable, SeekDB
from .embed import Embedder, EmbeddingError
from .ingest import parse_corpus
from .llm import Answer, LLMClient, answer_question
from .memory import Memory
from .pipeline import Pipeline

FALLBACK_SUGGESTIONS = [
    "Release 模式的编译产物在哪个目录？",
    "怎么用 GDB 调试 seekdb？",
    "memory_budget 设置为 0M 时如何计算？",
    "为什么 seekdb 禁止使用 STL 容器？",
]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    doc_id: int | None = None
    style: Literal["concise", "detailed"] = "concise"


class DeleteRequest(BaseModel):
    doc_id: int


class NoteRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=20000)
    citations: list[dict[str, Any]] = Field(default_factory=list)


class ProfileRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    style: str = Field(default="", max_length=100)
    goal: str = Field(default="", max_length=500)


def _evidence_summary(answer: Answer) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in answer.evidence:
        rows.append(
            {
                "n": row.get("n"),
                "chunk_id": row.get("chunk_id"),
                "doc_id": row.get("doc_id"),
                "doc_title": row.get("doc_title"),
                "source": row.get("source"),
                "heading": row.get("heading"),
                "page": row.get("page"),
                "vec_rank": row.get("vec_rank"),
                "ft_rank": row.get("ft_rank"),
                "vec_sim": row.get("vec_sim"),
                "ft_score": row.get("ft_score"),
                "rrf": row.get("rrf"),
                "used": row.get("used"),
            }
        )
    return rows


def _gold_questions(settings: Settings, limit: int = 4) -> list[str]:
    try:
        data = json.loads(settings.gold_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return FALLBACK_SUGGESTIONS[:limit]
    items = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return FALLBACK_SUGGESTIONS[:limit]
    questions = [
        str(item["question"])
        for item in items
        if isinstance(item, dict) and item.get("answerable", True) and item.get("question")
    ]
    return (questions or FALLBACK_SUGGESTIONS)[:limit]


def create_app(
    settings: Settings | None = None,
    *,
    db: SeekDB | None = None,
    embedder: Embedder | None = None,
    llm: LLMClient | None = None,
    memory: Memory | None = None,
    pipeline: Pipeline | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    db = db or SeekDB(settings)
    embedder = embedder or Embedder(settings)
    llm = llm or LLMClient(settings)
    memory = memory or Memory(settings)
    pipeline = pipeline or Pipeline(settings, db=db, embedder=embedder)

    app = FastAPI(title="据典 Judian", version="1.0.0")
    app.state.settings = settings
    app.state.db = db
    app.state.embedder = embedder
    app.state.llm = llm
    app.state.memory = memory
    app.state.pipeline = pipeline

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        path = settings.static_dir / "index.html"
        if not path.exists():
            raise HTTPException(status_code=404, detail="前端未安装")
        return FileResponse(path)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        def probe_db() -> dict[str, Any]:
            ok, detail = db.ping()
            if not ok:
                return {"db": "down", "db_error": detail, "corpus": None}
            try:
                stats = db.stats()
            except Exception as exc:
                return {"db": "ok", "db_error": str(exc), "corpus": None}
            return {"db": "ok", "db_error": None, "corpus": stats}

        def probe_memory() -> dict[str, Any]:
            ok, detail = memory.health()
            return {
                "memory": "ok" if ok else "local",
                "memory_detail": detail,
                "memory_source": "powercontext" if ok else "local",
            }

        probes = {
            "db": probe_db,
            "vector": lambda: _health_pair(embedder.available, "向量模型"),
            "llm": lambda: _health_pair(llm.available, "生成模型"),
            "memory": probe_memory,
        }
        results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {name: pool.submit(fn) for name, fn in probes.items()}
            for name, future in futures.items():
                try:
                    results[name] = future.result(timeout=8)
                except Exception as exc:
                    results[name] = {"error": str(exc)}
        db_part = results.get("db") or {}
        vector_part = results.get("vector") or {}
        llm_part = results.get("llm") or {}
        memory_part = results.get("memory") or {}
        payload = {
            "ok": db_part.get("db") == "ok",
            "db": db_part.get("db", "down"),
            "db_error": db_part.get("db_error"),
            "corpus": db_part.get("corpus"),
            "vector": vector_part.get("ok", False),
            "vector_detail": vector_part.get("detail", ""),
            "llm": llm_part.get("ok", False),
            "llm_detail": llm_part.get("detail", ""),
            "memory": memory_part.get("memory", "local"),
            "memory_detail": memory_part.get("memory_detail", ""),
            "memory_source": memory_part.get("memory_source", "local"),
            "models": {
                "embedding": settings.embed_model,
                "llm": settings.llm_model,
            },
        }
        return payload

    @app.get("/api/bootstrap")
    def bootstrap() -> dict[str, Any]:
        payload: dict[str, Any] = {
            "user": settings.user,
            "documents": [],
            "stats": None,
            "memory": memory.snapshot(),
            "history": [],
            "suggestions": _gold_questions(settings),
            "db": "down",
            "error": None,
        }
        try:
            payload["documents"] = db.list_documents()
            payload["stats"] = db.stats()
            payload["history"] = db.history(limit=12)
            payload["db"] = "ok"
        except DBUnavailable as exc:
            payload["error"] = str(exc)
        except Exception as exc:
            payload["error"] = str(exc)
        return payload

    @app.post("/api/ask")
    def ask(request: AskRequest) -> dict[str, Any]:
        question = request.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="问题不能为空")
        started = time.perf_counter()

        query_vector: list[float] | None = None
        embedding_error: str | None = None
        try:
            query_vector = embedder.embed_query(question)
        except EmbeddingError as exc:
            embedding_error = str(exc)

        try:
            retrieved = db.hybrid_search(question, query_vector, doc_id=request.doc_id)
        except DBUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        profile = None
        memory_snapshot: dict[str, Any] | None = None
        try:
            memory_snapshot = memory.snapshot()
            profile = memory_snapshot.get("profile")
        except Exception:
            memory_snapshot = None

        answer = answer_question(
            question,
            retrieved["fused"],
            retrieved["vector"],
            retrieved["fulltext"],
            settings=settings,
            llm=llm,
            corpus_contains=db.corpus_contains,
            profile=profile if isinstance(profile, dict) else None,
            style=request.style,
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        evidence_summary = _evidence_summary(answer)
        if settings.log_queries:
            db.log_query(
                user_id=settings.user,
                question=question,
                answer=answer.answer,
                mode=answer.mode,
                confidence=answer.confidence,
                citations=answer.citations,
                evidence=evidence_summary,
                elapsed_ms=elapsed_ms,
            )

        memory_result: dict[str, Any] = {"source": "local"}
        try:
            memory_result = memory.record_progress(
                question=question,
                confidence=answer.confidence,
                mode=answer.mode,
                docs=[str(c.get("doc_title") or "") for c in answer.citations],
                insufficient=answer.mode == "insufficient",
            )
        except Exception as exc:
            memory_result = {"source": "local", "error": str(exc)}

        payload = answer.to_dict()
        payload.update(
            {
                "question": question,
                "doc_id": request.doc_id,
                "elapsed_ms": elapsed_ms,
                "embedding": {
                    "used": query_vector is not None,
                    "model": settings.embed_model,
                    "error": embedding_error,
                },
                "memory": {
                    "source": memory_result.get("source", "local"),
                    "connected": memory_result.get("source") == "powercontext",
                },
                "evidence": answer.evidence,
            }
        )
        return payload

    @app.post("/api/import")
    def import_document(file: UploadFile = File(...)) -> dict[str, Any]:
        if not file.filename:
            raise HTTPException(status_code=400, detail="缺少文件名")
        limit = settings.max_upload_mb * 1024 * 1024
        data = file.file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过 {settings.max_upload_mb}MB 限制",
            )
        if not data:
            raise HTTPException(status_code=400, detail="文件为空")
        try:
            report = pipeline.ingest_upload(file.filename, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except EmbeddingError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except DBUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        payload = {"ok": True}
        payload.update(report.to_dict())
        return payload

    @app.post("/api/library/delete")
    def delete_document(request: DeleteRequest) -> dict[str, Any]:
        try:
            deleted = db.delete_document(request.doc_id)
        except DBUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="资料不存在")
        return {"ok": True, "doc_id": request.doc_id}

    @app.get("/api/history")
    def history(limit: int = 20) -> dict[str, Any]:
        limit = max(1, min(limit, 100))
        try:
            return {"items": db.history(limit=limit)}
        except DBUnavailable as exc:
            return JSONResponse(
                status_code=200,
                content={"items": [], "error": str(exc)},
            )

    @app.get("/api/notes")
    def list_notes(limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        try:
            return {"items": db.notes(limit=limit)}
        except DBUnavailable as exc:
            return JSONResponse(
                status_code=200,
                content={"items": [], "error": str(exc)},
            )

    @app.post("/api/notes")
    def add_note(request: NoteRequest) -> dict[str, Any]:
        valid = [
            item
            for item in request.citations
            if isinstance(item, dict) and item.get("chunk_id") is not None
        ]
        try:
            note = db.add_note(
                user_id=settings.user,
                question=request.question.strip(),
                answer=request.answer,
                citations=valid,
            )
        except DBUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True, "note": note}

    @app.get("/api/memory")
    def get_memory() -> dict[str, Any]:
        return memory.snapshot()

    @app.post("/api/memory/profile")
    def save_profile(request: ProfileRequest) -> dict[str, Any]:
        topic = request.topic.strip()
        if not topic:
            raise HTTPException(status_code=400, detail="研究主题不能为空")
        result = memory.save_profile(topic, request.style, request.goal)
        return {"ok": True, **result}

    @app.get("/api/eval")
    def eval_report() -> dict[str, Any]:
        path = settings.evidence_dir / "eval.json"
        if not path.exists():
            return {
                "available": False,
                "message": "尚未生成评测结果，请先运行 .venv/bin/python scripts/eval.py",
            }
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"available": False, "message": f"评测结果读取失败: {exc}"}
        if isinstance(data, dict):
            data.setdefault("available", True)
            return data
        return {"available": False, "message": "评测结果格式不正确"}

    @app.get("/api/documents")
    def documents() -> dict[str, Any]:
        try:
            return {"items": db.list_documents()}
        except DBUnavailable as exc:
            return JSONResponse(
                status_code=200,
                content={"items": [], "error": str(exc)},
            )

    @app.get("/api/corpus")
    def corpus() -> dict[str, Any]:
        items = []
        try:
            for document in parse_corpus(settings.corpus_dir):
                items.append(
                    {
                        "source": document.source,
                        "title": document.title,
                        "kind": document.kind,
                        "chars": document.chars,
                        "chunks": len(document.chunks),
                    }
                )
        except Exception as exc:
            return {"items": [], "error": str(exc)}
        return {"items": items}

    static_dir = settings.static_dir
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


def _health_pair(probe: Any, label: str) -> dict[str, Any]:
    try:
        ok, detail = probe()
    except Exception as exc:
        return {"ok": False, "detail": f"{label}检查失败: {exc}"}
    return {"ok": bool(ok), "detail": str(detail)}


app = create_app()
