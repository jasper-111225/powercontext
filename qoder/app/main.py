"""据典 · FastAPI 应用。"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .config import DATA_DIR, STATIC_DIR, settings
from .embed import Embedder
from .pipeline import IngestError, ingest_path as run_ingest
from .llm import Answerer, _terms
from .memory import Memory, PROGRESS_KIND, PROFILE_KIND

app = FastAPI(title="据典 Judian", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

embedder = Embedder()
answerer = Answerer()
memory = Memory()

UPLOAD_DIR = DATA_DIR / "uploads"
ALLOWED = {".md", ".markdown", ".txt", ".pdf", ".html", ".htm"}
MAX_UPLOAD = 20 * 1024 * 1024

SUGGESTIONS = [
    "seekdb 首次编译前需要准备什么？",
    "编译产物在哪个目录？支持 Debug 模式吗？",
    "ObIArray 和 ObArray 有什么区别？",
    "单元测试和 mysqltest 分别怎么跑？",
    "ObArenaAllocator 适合什么场景？",
    "C++ 类名和成员变量怎么命名？",
]


def _conn():
    return db.connect()


@app.on_event("startup")
def startup() -> None:
    settings.ensure_runtime()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    checks: dict[str, Any] = {"app": "ok", "db": "down", "embed": "unknown", "llm": "unknown"}
    try:
        conn = _conn()
        try:
            stats = db.corpus_stats(conn)
            checks["db"] = "ok"
            checks["corpus"] = stats
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        checks["db"] = f"{type(exc).__name__}: {exc}"
    checks["embed"] = f"{settings.embed_model} @ {settings.embed_base}（缓存 {embedder.cached} 条）"
    checks["llm"] = f"{settings.llm_model} @ {settings.llm_base}"
    checks["memory"] = "ok" if memory.health() else f"offline: {memory.last_error}"
    return checks


class AskBody(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    doc_id: int | None = None
    style: str | None = None


@app.post("/api/ask")
def ask(body: AskBody) -> dict[str, Any]:
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "问题不能为空")
    started = time.time()
    conn = _conn()
    try:
        if db.corpus_stats(conn)["chunks"] == 0:
            raise HTTPException(409, "资料库还是空的，请先导入资料或运行 scripts/seed.py")

        profile = (memory.snapshot().get("profile") or {}) if memory.health() else {}
        style = body.style or profile.get("style") or "concise"

        t_embed = time.time()
        vector = embedder.embed_one(question)
        embed_ms = int((time.time() - t_embed) * 1000)

        t_search = time.time()
        result = db.hybrid_search(conn, question, vector, doc_id=body.doc_id)
        corpus_text = db.corpus_text(conn)
        search_ms = int((time.time() - t_search) * 1000)
        evidence = result["fused"]

        terms = sorted(_terms(question))
        coverage = 0.0
        if terms and evidence:
            top_text = " ".join(item["text"] for item in evidence[:3]).lower()
            coverage = sum(1 for term in terms if term in top_text) / len(terms)
        top_sim = max((item.get("vec_sim") or 0) for item in evidence) if evidence else 0.0
        confidence = round(min(1.0, max(0.0, top_sim * 0.75 + coverage * 0.45)), 3)

        t_answer = time.time()
        answer = answerer.answer(question, evidence, style=style, corpus_text=corpus_text)
        answer_ms = int((time.time() - t_answer) * 1000)

        total_ms = int((time.time() - started) * 1000)
        used = set(answer.citations)
        public_evidence = []
        for index, item in enumerate(evidence, start=1):
            public_evidence.append(
                {
                    "index": index,
                    "chunk_id": int(item["id"]),
                    "doc_id": int(item["doc_id"]),
                    "doc_title": item.get("doc_title", ""),
                    "source": item.get("source", ""),
                    "heading": item.get("heading", ""),
                    "page": item.get("page"),
                    "text": item["text"],
                    "snippet": item["text"][:420] + ("……" if len(item["text"]) > 420 else ""),
                    "vec_rank": item.get("vec_rank"),
                    "ft_rank": item.get("ft_rank"),
                    "vec_sim": item.get("vec_sim"),
                    "ft_score": item.get("ft_score"),
                    "rrf": item.get("rrf"),
                    "used": index in used,
                }
            )

        cited_docs = []
        for index in sorted(used):
            if 1 <= index <= len(public_evidence):
                title = public_evidence[index - 1]["doc_title"]
                if title not in cited_docs:
                    cited_docs.append(title)

        query_id = db.log_query(
            conn,
            user_id=settings.user_id,
            question=question,
            answer=answer.text,
            mode=answer.mode,
            citations=[public_evidence[i - 1] for i in sorted(used) if 1 <= i <= len(public_evidence)],
            confidence=confidence,
            latency_ms=total_ms,
        )

        memory_result: dict[str, Any] = {}
        try:
            memory_result = memory.record_ask(
                question=question,
                confidence=confidence,
                answer_mode=answer.mode,
                cited_docs=cited_docs,
                weak_topic=answer.mode == "insufficient" or confidence < 0.35,
            )
        except Exception as exc:  # noqa: BLE001
            memory_result = {"source": "error", "error": f"{type(exc).__name__}: {exc}"}

        return {
            "query_id": query_id,
            "question": question,
            "answer": answer.text,
            "mode": answer.mode,
            "reason": answer.reason,
            "style": style,
            "confidence": confidence,
            "coverage": round(coverage, 3),
            "citations": sorted(used),
            "evidence": public_evidence,
            "trace": {
                "vector": [
                    {
                        "rank": row["vec_rank"],
                        "chunk_id": int(row["id"]),
                        "doc_title": next(
                            (e["doc_title"] for e in public_evidence if e["chunk_id"] == int(row["id"])),
                            "",
                        ),
                        "heading": row.get("heading", ""),
                        "vec_sim": row["vec_sim"],
                        "snippet": row["text"][:120],
                    }
                    for row in result["vector"][:10]
                ],
                "fulltext": [
                    {
                        "rank": row["ft_rank"],
                        "chunk_id": int(row["id"]),
                        "doc_title": next(
                            (e["doc_title"] for e in public_evidence if e["chunk_id"] == int(row["id"])),
                            "",
                        ),
                        "heading": row.get("heading", ""),
                        "ft_score": row["ft_score"],
                        "snippet": row["text"][:120],
                    }
                    for row in result["fulltext"][:10]
                ],
                "params": result["params"],
                "fused_total": result["fused_total"],
            },
            "timings": {"embed_ms": embed_ms, "search_ms": search_ms, "answer_ms": answer_ms, "total_ms": total_ms},
            "memory": memory_result,
            "terms": terms,
        }
    finally:
        conn.close()


@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    conn = _conn()
    try:
        stats = db.corpus_stats(conn)
        documents = [
            {
                **doc,
                "created_at": doc["created_at"].isoformat(sep=" ") if doc.get("created_at") else "",
            }
            for doc in db.list_documents(conn)
        ]
        history = db.list_history(conn, user_id=settings.user_id)
    finally:
        conn.close()
    return {
        "app": {"name": "据典", "version": "0.1.0"},
        "models": {"embed": settings.embed_model, "llm": settings.llm_model},
        "corpus": stats,
        "documents": documents,
        "memory": memory.snapshot(),
        "history": history,
        "suggestions": SUGGESTIONS,
        "memory_kinds": {"profile": PROFILE_KIND, "progress": PROGRESS_KIND},
    }


@app.post("/api/import")
async def import_file(file: UploadFile = File(...)) -> dict[str, Any]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, f"只支持 {', '.join(sorted(ALLOWED))} 格式")
    content = await file.read()
    if len(content) > MAX_UPLOAD:
        raise HTTPException(400, "文件超过 20MB 限制")
    if not content:
        raise HTTPException(400, "文件为空")
    safe_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", Path(file.filename or "upload").name)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = UPLOAD_DIR / f"{int(time.time())}-{safe_name}"
    target.write_bytes(content)
    try:
        summary = run_ingest(target, embedder, title=Path(safe_name).stem)
    except IngestError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **summary}


@app.post("/api/library/delete")
def delete_document(body: dict[str, int]) -> dict[str, Any]:
    doc_id = int(body.get("doc_id", 0))
    if not doc_id:
        raise HTTPException(400, "缺少 doc_id")
    conn = _conn()
    try:
        db.delete_document(conn, doc_id)
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/history")
def history(limit: int = 20) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        return db.list_history(conn, user_id=settings.user_id, limit=min(limit, 100))
    finally:
        conn.close()


class NoteBody(BaseModel):
    question: str
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)


@app.post("/api/notes")
def create_note(body: NoteBody) -> dict[str, Any]:
    conn = _conn()
    try:
        note_id = db.add_note(
            conn,
            user_id=settings.user_id,
            question=body.question[:500],
            answer=body.answer[:8000],
            citations=body.citations,
        )
    finally:
        conn.close()
    return {"ok": True, "note_id": note_id}


@app.get("/api/notes")
def notes() -> list[dict[str, Any]]:
    conn = _conn()
    try:
        return db.list_notes(conn, user_id=settings.user_id)
    finally:
        conn.close()


@app.get("/api/memory")
def get_memory() -> dict[str, Any]:
    return memory.snapshot()


class ProfileBody(BaseModel):
    topic: str = ""
    style: str = "concise"
    goal: str = ""


@app.post("/api/memory/profile")
def set_profile(body: ProfileBody) -> dict[str, Any]:
    result = memory.save_profile(topic=body.topic, style=body.style, goal=body.goal)
    return {"ok": True, **result, "snapshot": memory.snapshot()}


@app.get("/api/eval")
def evaluation() -> JSONResponse:
    path = Path(__file__).resolve().parent.parent / "docs" / "evidence" / "eval.json"
    if not path.exists():
        return JSONResponse({"available": False, "hint": "运行 scripts/eval.py 生成评测结果"})
    import json

    return JSONResponse({"available": True, **json.loads(path.read_text(encoding="utf-8"))})
