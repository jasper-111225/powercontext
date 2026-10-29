from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator, Sequence

import pymysql
from pymysql.cursors import DictCursor

from .config import Settings
from .ingest import ParsedDocument


class DBUnavailable(RuntimeError):
    pass


DOCUMENTS_DDL = """
CREATE TABLE IF NOT EXISTS documents (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  title VARCHAR(200) NOT NULL,
  source VARCHAR(512) NOT NULL,
  kind VARCHAR(16) NOT NULL,
  pages INT NULL,
  chars INT NOT NULL DEFAULT 0,
  checksum VARCHAR(64) NOT NULL DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_doc_source (source(191))
) ORGANIZATION = HEAP
"""

CHUNKS_DDL = """
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
) ORGANIZATION = HEAP
"""

QUERIES_DDL = """
CREATE TABLE IF NOT EXISTS queries (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL DEFAULT 'demo',
  question TEXT NOT NULL,
  answer MEDIUMTEXT,
  mode VARCHAR(16) NOT NULL DEFAULT '',
  confidence DOUBLE NOT NULL DEFAULT 0,
  citations JSON,
  evidence JSON,
  elapsed_ms INT NOT NULL DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_queries_user_time (user_id, created_at)
) ORGANIZATION = HEAP
"""

NOTES_DDL = """
CREATE TABLE IF NOT EXISTS notes (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL DEFAULT 'demo',
  question TEXT NOT NULL,
  answer MEDIUMTEXT,
  citations JSON,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_notes_user_time (user_id, created_at)
) ORGANIZATION = HEAP
"""

VECTOR_SEARCH_SQL = """
SELECT c.id, c.doc_id, c.ordinal, c.heading, c.page, c.text,
       d.title AS doc_title, d.source, d.kind AS doc_kind,
       1 - cosine_distance(c.embedding, %s) AS vec_sim
FROM chunks c
JOIN documents d ON d.id = c.doc_id
{where}
ORDER BY cosine_distance(c.embedding, %s) ASC
LIMIT %s
"""

FULLTEXT_SEARCH_SQL = """
SELECT c.id, c.doc_id, c.ordinal, c.heading, c.page, c.text,
       d.title AS doc_title, d.source, d.kind AS doc_kind,
       MATCH(c.text) AGAINST(%s IN NATURAL LANGUAGE MODE) AS ft_score
FROM chunks c
JOIN documents d ON d.id = c.doc_id
WHERE MATCH(c.text) AGAINST(%s IN NATURAL LANGUAGE MODE)
{and_doc}
ORDER BY ft_score DESC
LIMIT %s
"""


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{value:.7g}" for value in vector) + "]"


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ")
    return value


def _row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: _jsonable(value) for key, value in row.items()}


class SeekDB:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @contextmanager
    def connect(self, with_database: bool = True) -> Iterator[pymysql.connections.Connection]:
        try:
            connection = pymysql.connect(
                host=self.settings.db_host,
                port=self.settings.db_port,
                user=self.settings.db_user,
                password=self.settings.db_password,
                database=self.settings.db_name if with_database else None,
                charset="utf8mb4",
                cursorclass=DictCursor,
                autocommit=True,
                connect_timeout=5,
                read_timeout=30,
                write_timeout=30,
            )
        except Exception as exc:
            raise DBUnavailable(f"seekdb 连接失败: {exc}") from exc
        try:
            yield connection
        finally:
            connection.close()

    def ensure_database(self) -> None:
        with self.connect(with_database=False) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.settings.db_name}` "
                    "DEFAULT CHARACTER SET utf8mb4"
                )

    def ensure_schema(self) -> None:
        statements = [
            DOCUMENTS_DDL,
            CHUNKS_DDL.format(dim=self.settings.embed_dim),
            QUERIES_DDL,
            NOTES_DDL,
        ]
        with self.connect() as connection:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)

    def ping(self) -> tuple[bool, str]:
        # 服务器级探活：不指定 database，避免首次部署时目标库尚未创建就误报
        # "Unknown database"，导致 setup/seed 还没走到 ensure_database 就失败。
        try:
            with self.connect(with_database=False) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT VERSION() AS version")
                    row = cursor.fetchone()
            return True, str(row["version"]) if row else "connected"
        except Exception as exc:
            return False, str(exc)

    # ---------------------------------------------------------------- documents
    def get_document(self, source: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT d.id, d.source, d.checksum, d.title, d.kind, d.pages, d.chars, "
                    "(SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS chunks "
                    "FROM documents d WHERE d.source=%s",
                    (source,),
                )
                return _row(cursor.fetchone())

    def list_documents(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT d.id, d.title, d.source, d.kind, d.pages, d.chars, "
                    "d.checksum, d.created_at, "
                    "(SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS chunks "
                    "FROM documents d ORDER BY d.id"
                )
                return [_row(row) for row in cursor.fetchall()]

    def replace_document(self, document: ParsedDocument, embeddings: Sequence[Sequence[float]]) -> dict[str, Any]:
        if len(embeddings) != len(document.chunks):
            raise ValueError("embedding 数量与分块数量不一致")
        if not document.chunks:
            raise ValueError(f"文档没有可入库的片段: {document.source}")

        payload = [
            (
                chunk.ordinal,
                chunk.heading[:255],
                chunk.page,
                chunk.text,
                vector_literal(vector),
            )
            for chunk, vector in zip(document.chunks, embeddings)
        ]

        with self.connect() as connection:
            connection.autocommit(False)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id FROM documents WHERE source=%s FOR UPDATE",
                        (document.source,),
                    )
                    row = cursor.fetchone()
                    if row:
                        doc_id = int(row["id"])
                        cursor.execute("DELETE FROM chunks WHERE doc_id=%s", (doc_id,))
                        cursor.execute(
                            "UPDATE documents SET title=%s, kind=%s, pages=%s, chars=%s, "
                            "checksum=%s WHERE id=%s",
                            (
                                document.title[:200],
                                document.kind,
                                document.pages,
                                document.chars,
                                document.checksum,
                                doc_id,
                            ),
                        )
                        replaced = True
                    else:
                        cursor.execute(
                            "INSERT INTO documents "
                            "(title, source, kind, pages, chars, checksum) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            (
                                document.title[:200],
                                document.source[:512],
                                document.kind,
                                document.pages,
                                document.chars,
                                document.checksum,
                            ),
                        )
                        doc_id = int(cursor.lastrowid)
                        replaced = False

                    cursor.executemany(
                        "INSERT INTO chunks "
                        "(doc_id, ordinal, heading, page, text, embedding) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        [
                            (doc_id, ordinal, heading, page, text, vector)
                            for ordinal, heading, page, text, vector in payload
                        ],
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return {
            "doc_id": doc_id,
            "title": document.title,
            "source": document.source,
            "kind": document.kind,
            "chars": document.chars,
            "chunks": len(document.chunks),
            "replaced": replaced,
        }

    def delete_document(self, doc_id: int) -> bool:
        with self.connect() as connection:
            connection.autocommit(False)
            try:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM chunks WHERE doc_id=%s", (doc_id,))
                    cursor.execute("DELETE FROM documents WHERE id=%s", (doc_id,))
                    deleted = cursor.rowcount > 0
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return deleted

    def reset_corpus(self) -> None:
        with self.connect() as connection:
            connection.autocommit(False)
            try:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM chunks")
                    cursor.execute("DELETE FROM documents")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def stats(self) -> dict[str, Any]:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) AS documents, COALESCE(SUM(chars),0) AS chars "
                    "FROM documents"
                )
                docs = cursor.fetchone() or {}
                cursor.execute(
                    "SELECT COUNT(*) AS chunks, "
                    "COALESCE(AVG(CHAR_LENGTH(text)),0) AS avg_chunk_chars FROM chunks"
                )
                chunks = cursor.fetchone() or {}
        return {
            "documents": int(docs.get("documents") or 0),
            "chunks": int(chunks.get("chunks") or 0),
            "chars": int(docs.get("chars") or 0),
            "avg_chunk_chars": round(float(chunks.get("avg_chunk_chars") or 0), 1),
        }

    def corpus_contains(self, term: str) -> bool:
        if not term:
            return True
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 AS hit FROM chunks "
                    "WHERE LOCATE(LOWER(%s), LOWER(text)) > 0 LIMIT 1",
                    (term,),
                )
                return cursor.fetchone() is not None

    def fetch_chunks(self, chunk_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        if not chunk_ids:
            return {}
        placeholders = ",".join(["%s"] * len(chunk_ids))
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT c.id, c.doc_id, c.ordinal, c.heading, c.page, c.text, "
                    "d.title AS doc_title, d.source "
                    f"FROM chunks c JOIN documents d ON d.id=c.doc_id "
                    f"WHERE c.id IN ({placeholders})",
                    tuple(chunk_ids),
                )
                return {int(row["id"]): _row(row) for row in cursor.fetchall()}

    # ---------------------------------------------------------------- retrieval
    def vector_search(
        self,
        vector: Sequence[float],
        limit: int | None = None,
        doc_id: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = limit or self.settings.recall_limit
        literal = vector_literal(vector)
        where = "WHERE c.doc_id=%s" if doc_id is not None else ""
        sql = VECTOR_SEARCH_SQL.format(where=where)
        params: list[Any] = [literal]
        if doc_id is not None:
            params.append(doc_id)
        params.extend([literal, limit])
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, tuple(params))
                return [_row(row) for row in cursor.fetchall()]

    def fulltext_search(
        self,
        question: str,
        limit: int | None = None,
        doc_id: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = limit or self.settings.recall_limit
        and_doc = "AND c.doc_id=%s" if doc_id is not None else ""
        sql = FULLTEXT_SEARCH_SQL.format(and_doc=and_doc)
        params: list[Any] = [question]
        if doc_id is not None:
            params.append(doc_id)
        params.extend([question, limit])
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, tuple(params))
                return [_row(row) for row in cursor.fetchall()]

    def hybrid_search(
        self,
        question: str,
        vector: Sequence[float] | None,
        limit: int | None = None,
        doc_id: int | None = None,
    ) -> dict[str, Any]:
        limit = limit or self.settings.recall_limit
        vector_rows = (
            self.vector_search(vector, limit=limit, doc_id=doc_id) if vector else []
        )
        fulltext_rows = self.fulltext_search(question, limit=limit, doc_id=doc_id)

        merged: dict[int, dict[str, Any]] = {}
        k = self.settings.rrf_k
        for rank, row in enumerate(vector_rows, start=1):
            row = dict(row)
            row["vec_rank"] = rank
            row["ft_rank"] = None
            row["ft_score"] = None
            row["rrf"] = 1.0 / (k + rank)
            merged[int(row["id"])] = row
        for rank, row in enumerate(fulltext_rows, start=1):
            chunk_id = int(row["id"])
            if chunk_id in merged:
                merged[chunk_id]["ft_rank"] = rank
                merged[chunk_id]["ft_score"] = row.get("ft_score")
                merged[chunk_id]["rrf"] += 1.0 / (k + rank)
            else:
                row = dict(row)
                row["vec_rank"] = None
                row["vec_sim"] = None
                row["ft_rank"] = rank
                row["rrf"] = 1.0 / (k + rank)
                merged[chunk_id] = row

        fused = sorted(
            merged.values(),
            key=lambda item: (
                -float(item.get("rrf") or 0),
                item.get("vec_rank") or 10**9,
                item.get("ft_rank") or 10**9,
            ),
        )
        return {"vector": vector_rows, "fulltext": fulltext_rows, "fused": fused, "rrf_k": k}

    # ----------------------------------------------------------------- history
    def log_query(
        self,
        *,
        user_id: str,
        question: str,
        answer: str,
        mode: str,
        confidence: float,
        citations: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        elapsed_ms: int,
    ) -> int | None:
        try:
            with self.connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO queries "
                        "(user_id, question, answer, mode, confidence, citations, evidence, elapsed_ms) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            user_id,
                            question,
                            answer,
                            mode,
                            confidence,
                            json.dumps(citations, ensure_ascii=False),
                            json.dumps(evidence, ensure_ascii=False),
                            elapsed_ms,
                        ),
                    )
                    return int(cursor.lastrowid)
        except Exception:
            return None

    def history(self, limit: int = 20, user_id: str | None = None) -> list[dict[str, Any]]:
        user_id = user_id or self.settings.user
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, user_id, question, answer, mode, confidence, citations, "
                    "elapsed_ms, created_at FROM queries "
                    "WHERE user_id=%s ORDER BY id DESC LIMIT %s",
                    (user_id, limit),
                )
                rows = cursor.fetchall()
        for row in rows:
            if isinstance(row.get("citations"), str):
                try:
                    row["citations"] = json.loads(row["citations"])
                except json.JSONDecodeError:
                    row["citations"] = []
            row["created_at"] = _jsonable(row.get("created_at"))
        return rows

    # ------------------------------------------------------------------- notes
    def add_note(
        self,
        *,
        user_id: str,
        question: str,
        answer: str,
        citations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO notes (user_id, question, answer, citations) "
                    "VALUES (%s, %s, %s, %s)",
                    (user_id, question, answer, json.dumps(citations, ensure_ascii=False)),
                )
                note_id = int(cursor.lastrowid)
        return {"id": note_id, "question": question, "answer": answer, "citations": citations}

    def notes(self, limit: int = 50, user_id: str | None = None) -> list[dict[str, Any]]:
        user_id = user_id or self.settings.user
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, question, answer, citations, created_at FROM notes "
                    "WHERE user_id=%s ORDER BY id DESC LIMIT %s",
                    (user_id, limit),
                )
                rows = cursor.fetchall()
        for row in rows:
            if isinstance(row.get("citations"), str):
                try:
                    row["citations"] = json.loads(row["citations"])
                except json.JSONDecodeError:
                    row["citations"] = []
            row["created_at"] = _jsonable(row.get("created_at"))
        return rows
