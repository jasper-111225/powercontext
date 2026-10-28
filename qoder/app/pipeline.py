"""导入流水线：解析 → 分块 → 向量化 → 写入 seekdb。

单独成模块，命令行种子脚本和 HTTP 上传共用同一条路径，
避免「网页导入能用、脚本灌库不能用」这类两套逻辑的问题。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import db
from .config import settings
from .embed import Embedder
from .ingest import checksum, parse_file


class IngestError(ValueError):
    pass


def ingest_path(path: Path, embedder: Embedder, *, title: str | None = None) -> dict[str, Any]:
    parsed = parse_file(path, title=title)
    if not parsed.chunks:
        raise IngestError(f"没有从 {path.name} 解析出可用文本")
    vectors = embedder.embed(chunk.text for chunk in parsed.chunks)
    payload = [
        {
            "ordinal": chunk.ordinal,
            "heading": chunk.heading,
            "page": chunk.page,
            "text": chunk.text,
            "embedding": vector,
        }
        for chunk, vector in zip(parsed.chunks, vectors)
    ]
    digest = checksum(path)
    conn = db.connect()
    try:
        doc_id = db.upsert_document(
            conn,
            title=parsed.title,
            source=str(path.resolve()),
            kind=parsed.kind,
            pages=parsed.pages,
            chars=parsed.chars,
            checksum=digest,
        )
        db.insert_chunks(conn, doc_id, payload)
    finally:
        conn.close()
    return {
        "doc_id": doc_id,
        "title": parsed.title,
        "kind": parsed.kind,
        "pages": parsed.pages,
        "chars": parsed.chars,
        "chunks": len(payload),
        "source": str(path),
    }


def corpus_files(directory: Path) -> list[Path]:
    allowed = {".md", ".markdown", ".txt", ".pdf", ".html", ".htm"}
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file()
        and path.suffix.lower() in allowed
        and not path.name.startswith(".")
        and path.name not in {"SOURCES.md", "README.md"}
    )


__all__ = ["IngestError", "ingest_path", "corpus_files", "settings"]
