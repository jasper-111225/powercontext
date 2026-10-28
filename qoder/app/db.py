"""seekdb 数据层：文档、分块（向量 + IK 全文）、问答日志、笔记。

检索走 seekdb 的两条原生通路：
  1. `cosine_distance(embedding, ?)` 向量近邻；
  2. `MATCH(text) AGAINST(?)` 全文（IK 中文分词）。
两路各自取 top-K，在 Python 里用 RRF 融合。融合过程原样返回给前端，
评审可以直接看到 seekdb 在这一次回答里做了什么。
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  title VARCHAR(200) NOT NULL,
  source VARCHAR(512) NOT NULL,
  kind VARCHAR(16) NOT NULL DEFAULT 'text',
  pages INT NULL,
  chars INT NOT NULL DEFAULT 0,
  checksum VARCHAR(64) NOT NULL DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_doc_source (source(191))
) ORGANIZATION = HEAP;

CREATE TABLE IF NOT EXISTS chunks (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  doc_id BIGINT NOT NULL,
  ordinal INT NOT NULL DEFAULT 0,
  heading VARCHAR(255) NOT NULL DEFAULT '',
  page INT NULL,
  text TEXT NOT NULL,
  embedding VECTOR({dim}) NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_chunk_doc (doc_id, ordinal),
  FULLTEXT INDEX ft_chunk (text) WITH PARSER ik,
  VECTOR INDEX vi_chunk (embedding) WITH (DISTANCE=cosine, TYPE=hnsw, LIB=vsag)
) ORGANIZATION = HEAP;

CREATE TABLE IF NOT EXISTS queries (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL DEFAULT 'demo',
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  mode VARCHAR(16) NOT NULL DEFAULT 'llm',
  citations JSON NULL,
  confidence DOUBLE NOT NULL DEFAULT 0,
  latency_ms INT NOT NULL DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_query_user (user_id, id)
) ORGANIZATION = HEAP;

CREATE TABLE IF NOT EXISTS notes (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL DEFAULT 'demo',
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  citations JSON NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_note_user (user_id, id)
) ORGANIZATION = HEAP;
"""


def connect(*, with_db: bool = True) -> Connection:
    kwargs: dict[str, Any] = {
        "host": settings.db_host,
        "port": settings.db_port,
        "user": settings.db_user,
        "password": settings.db_password,
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": True,
        "connect_timeout": 8,
        "read_timeout": 120,
        "write_timeout": 120,
    }
    if with_db:
        kwargs["database"] = settings.db_name
    return pymysql.connect(**kwargs)


def init_db(*, reset: bool = False) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_]+", settings.db_name):
        raise ValueError(f"非法库名：{settings.db_name}")
    conn = connect(with_db=False)
    try:
        with conn.cursor() as cur:
            if reset:
                cur.execute(f"DROP DATABASE IF EXISTS `{settings.db_name}`")
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.db_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
            )
            cur.execute(f"USE `{settings.db_name}`")
            for statement in SCHEMA.format(dim=settings.embed_dim).split(";"):
                sql = statement.strip()
                if sql:
                    cur.execute(sql)
    finally:
        conn.close()


def vec_literal(vector: Iterable[float]) -> str:
    return "[" + ",".join(f"{float(x):.7f}" for x in vector) + "]"


# ------------------------------------------------------------------ documents
def upsert_document(
    conn: Connection,
    *,
    title: str,
    source: str,
    kind: str,
    pages: int | None,
    chars: int,
    checksum: str,
) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM documents WHERE source=%s", (source,))
        row = cur.fetchone()
        if row:
            doc_id = int(row["id"])
            cur.execute(
                "UPDATE documents SET title=%s, kind=%s, pages=%s, chars=%s, checksum=%s WHERE id=%s",
                (title, kind, pages, chars, checksum, doc_id),
            )
            cur.execute("DELETE FROM chunks WHERE doc_id=%s", (doc_id,))
            return doc_id
        cur.execute(
            "INSERT INTO documents (title, source, kind, pages, chars, checksum) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (title, source, kind, pages, chars, checksum),
        )
        return int(cur.lastrowid)


def insert_chunks(conn: Connection, doc_id: int, chunks: list[dict[str, Any]]) -> None:
    sql = (
        "INSERT INTO chunks (doc_id, ordinal, heading, page, text, embedding) "
        "VALUES (%s,%s,%s,%s,%s,%s)"
    )
    rows = [
        (
            doc_id,
            chunk["ordinal"],
            (chunk.get("heading") or "")[:250],
            chunk.get("page"),
            chunk["text"],
            vec_literal(chunk["embedding"]),
        )
        for chunk in chunks
    ]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def list_documents(conn: Connection) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.id, d.title, d.source, d.kind, d.pages, d.chars, d.created_at, "
            "COUNT(c.id) AS chunks "
            "FROM documents d LEFT JOIN chunks c ON c.doc_id=d.id "
            "GROUP BY d.id ORDER BY d.id"
        )
        return list(cur.fetchall())


def delete_document(conn: Connection, doc_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE doc_id=%s", (doc_id,))
        cur.execute("DELETE FROM documents WHERE id=%s", (doc_id,))


def corpus_text(conn: Connection, limit: int = 400_000) -> str:
    """全量片段文本，用于判断问句里的英文实体是否在语料中出现。"""
    with conn.cursor() as cur:
        cur.execute("SELECT heading, text FROM chunks")
        parts: list[str] = []
        size = 0
        for row in cur.fetchall():
            parts.append(f"{row['heading']} {row['text']}")
            size += len(row["text"])
            if size >= limit:
                break
    return "\n".join(parts)


def corpus_stats(conn: Connection) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM documents")
        docs = int(cur.fetchone()["n"])
        cur.execute("SELECT COUNT(*) AS n, AVG(CHAR_LENGTH(text)) AS avg_len FROM chunks")
        row = cur.fetchone()
        chunks = int(row["n"])
        avg_len = float(row["avg_len"] or 0)
        cur.execute("SELECT COALESCE(SUM(chars),0) AS n FROM documents")
        chars = int(cur.fetchone()["n"])
    return {"documents": docs, "chunks": chunks, "chars": chars, "avg_chunk_chars": round(avg_len, 1)}


# ------------------------------------------------------------------ retrieval
def vector_search(
    conn: Connection, query_vector: list[float], *, limit: int, doc_id: int | None = None
) -> list[dict[str, Any]]:
    literal = vec_literal(query_vector)
    sql = (
        "SELECT c.id, c.doc_id, c.ordinal, c.heading, c.page, c.text, "
        "1 - cosine_distance(c.embedding, %s) AS vec_sim "
        "FROM chunks c "
    )
    params: list[Any] = [literal]
    if doc_id:
        sql += "WHERE c.doc_id=%s "
        params.append(doc_id)
    sql += "ORDER BY cosine_distance(c.embedding, %s) ASC LIMIT %s"
    params.extend([literal, limit])
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = list(cur.fetchall())
    for rank, row in enumerate(rows, start=1):
        row["vec_rank"] = rank
        row["vec_sim"] = round(float(row["vec_sim"]), 4)
    return rows


def fulltext_search(
    conn: Connection, query: str, *, limit: int, doc_id: int | None = None
) -> list[dict[str, Any]]:
    sql = (
        "SELECT c.id, c.doc_id, c.ordinal, c.heading, c.page, c.text, "
        "MATCH(c.text) AGAINST(%s IN NATURAL LANGUAGE MODE) AS ft_score "
        "FROM chunks c WHERE MATCH(c.text) AGAINST(%s IN NATURAL LANGUAGE MODE) "
    )
    params: list[Any] = [query, query]
    if doc_id:
        sql += "AND c.doc_id=%s "
        params.append(doc_id)
    sql += "ORDER BY ft_score DESC LIMIT %s"
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = list(cur.fetchall())
    for rank, row in enumerate(rows, start=1):
        row["ft_rank"] = rank
        row["ft_score"] = round(float(row["ft_score"]), 4)
    return rows


def hybrid_search(
    conn: Connection,
    query: str,
    query_vector: list[float],
    *,
    doc_id: int | None = None,
    top_n: int | None = None,
) -> dict[str, Any]:
    top_n = top_n or settings.evidence_top_n
    vec_rows = vector_search(
        conn, query_vector, limit=settings.vector_top_k, doc_id=doc_id
    )
    ft_rows = fulltext_search(conn, query, limit=settings.fulltext_top_k, doc_id=doc_id)

    fused: dict[int, dict[str, Any]] = {}
    for row in vec_rows:
        item = fused.setdefault(
            int(row["id"]),
            {**row, "vec_rank": None, "ft_rank": None, "ft_score": None, "rrf": 0.0},
        )
        item["vec_rank"] = row["vec_rank"]
        item["vec_sim"] = row["vec_sim"]
        item["rrf"] += 1.0 / (settings.rrf_k + row["vec_rank"])
    for row in ft_rows:
        item = fused.setdefault(
            int(row["id"]),
            {**row, "vec_rank": None, "vec_sim": None, "rrf": 0.0},
        )
        item["ft_rank"] = row["ft_rank"]
        item["ft_score"] = row["ft_score"]
        item["rrf"] += 1.0 / (settings.rrf_k + row["ft_rank"])
    merged = sorted(fused.values(), key=lambda x: (-x["rrf"], -(x.get("vec_sim") or 0)))
    for rank, item in enumerate(merged, start=1):
        item["rank"] = rank
        item["rrf"] = round(item["rrf"], 6)
    docs = {int(d["id"]): d for d in list_documents(conn)}
    from .llm import _overlap, _terms

    question_terms = _terms(query)
    for item in merged:
        doc = docs.get(int(item["doc_id"]), {})
        item["doc_title"] = doc.get("title") or ""
        item["source"] = doc.get("source") or ""
        item["kind"] = doc.get("kind") or ""
        heading_text = f"{item['doc_title']} {item.get('heading', '')}"
        item["title_boost"] = round(_overlap(question_terms, heading_text) * 0.012, 6)
        item["rrf"] = round(item["rrf"] + item["title_boost"], 6)
    merged.sort(key=lambda x: (-x["rrf"], -(x.get("vec_sim") or 0)))
    for rank, item in enumerate(merged, start=1):
        item["rank"] = rank
    return {
        "vector": vec_rows,
        "fulltext": ft_rows,
        "fused": merged[:top_n],
        "fused_total": len(merged),
        "params": {
            "vector_top_k": settings.vector_top_k,
            "fulltext_top_k": settings.fulltext_top_k,
            "rrf_k": settings.rrf_k,
            "doc_id": doc_id,
        },
    }


def get_chunks(conn: Connection, ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ",".join(["%s"] * len(ids))
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, doc_id, ordinal, heading, page, text FROM chunks WHERE id IN ({placeholders})",
            ids,
        )
        rows = {int(r["id"]): r for r in cur.fetchall()}
    return [rows[i] for i in ids if i in rows]


# --------------------------------------------------------------------- history
def log_query(
    conn: Connection,
    *,
    user_id: str,
    question: str,
    answer: str,
    mode: str,
    citations: list[dict[str, Any]],
    confidence: float,
    latency_ms: int,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO queries (user_id, question, answer, mode, citations, confidence, latency_ms) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                user_id,
                question,
                answer,
                mode,
                json.dumps(citations, ensure_ascii=False),
                confidence,
                latency_ms,
            ),
        )
        return int(cur.lastrowid)


def list_history(conn: Connection, *, user_id: str, limit: int = 12) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, question, answer, mode, confidence, latency_ms, created_at "
            "FROM queries WHERE user_id=%s ORDER BY id DESC LIMIT %s",
            (user_id, limit),
        )
        rows = list(cur.fetchall())
    for row in rows:
        row["created_at"] = row["created_at"].isoformat(sep=" ") if row.get("created_at") else ""
    return rows


def add_note(
    conn: Connection, *, user_id: str, question: str, answer: str, citations: list[dict[str, Any]]
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notes (user_id, question, answer, citations) VALUES (%s,%s,%s,%s)",
            (user_id, question, answer, json.dumps(citations, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def list_notes(conn: Connection, *, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, question, answer, citations, created_at FROM notes "
            "WHERE user_id=%s ORDER BY id DESC LIMIT %s",
            (user_id, limit),
        )
        rows = list(cur.fetchall())
    for row in rows:
        if isinstance(row.get("citations"), str):
            row["citations"] = json.loads(row["citations"])
        row["created_at"] = row["created_at"].isoformat(sep=" ") if row.get("created_at") else ""
    return rows


def reset_corpus(conn: Connection) -> None:
    """清空资料与片段，保留问答历史/笔记。"""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chunks")
        cur.execute("DELETE FROM documents")
