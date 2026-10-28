from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Settings
from .db import SeekDB
from .embed import Embedder
from .ingest import ParsedDocument, parse_corpus, parse_document

_SAFE_NAME = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


@dataclass
class IngestReport:
    doc_id: int | None
    title: str
    source: str
    kind: str
    chars: int
    chunks: int
    skipped: bool = False
    replaced: bool = False

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "source": self.source,
            "kind": self.kind,
            "chars": self.chars,
            "chunks": self.chunks,
            "skipped": self.skipped,
            "replaced": self.replaced,
        }


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        db: SeekDB | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.settings = settings
        self.db = db or SeekDB(settings)
        self.embedder = embedder or Embedder(settings)

    def ingest_document(
        self,
        document: ParsedDocument,
        *,
        force: bool = False,
        progress: Callable[[int, int], None] | None = None,
    ) -> IngestReport:
        existing = self.db.get_document(document.source)
        if (
            existing
            and not force
            and existing.get("checksum") == document.checksum
            and int(existing.get("chunks") or 0) > 0
        ):
            return IngestReport(
                doc_id=int(existing["id"]),
                title=str(existing.get("title") or document.title),
                source=document.source,
                kind=str(existing.get("kind") or document.kind),
                chars=int(existing.get("chars") or document.chars),
                chunks=int(existing.get("chunks") or 0),
                skipped=True,
            )

        if not document.chunks:
            raise ValueError(f"文档没有可入库的片段: {document.source}")
        vectors = self.embedder.embed_texts(
            [chunk.text for chunk in document.chunks], progress=progress
        )
        saved = self.db.replace_document(document, vectors)
        return IngestReport(
            doc_id=saved["doc_id"],
            title=saved["title"],
            source=saved["source"],
            kind=saved["kind"],
            chars=saved["chars"],
            chunks=saved["chunks"],
            replaced=bool(saved["replaced"]),
        )

    def ingest_path(
        self,
        path: Path,
        *,
        source: str | None = None,
        title: str | None = None,
        force: bool = False,
    ) -> IngestReport:
        document = parse_document(
            path, source or path.relative_to(self.settings.root).as_posix(), title
        )
        return self.ingest_document(document, force=force)

    def ingest_corpus(
        self,
        *,
        only: str | None = None,
        limit: int | None = None,
        reset: bool = False,
        force: bool = False,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[IngestReport]:
        if reset:
            self.db.reset_corpus()
        documents = parse_corpus(self.settings.corpus_dir, only=only, limit=limit)
        reports: list[IngestReport] = []
        for index, document in enumerate(documents, start=1):
            if progress:
                progress(index - 1, len(documents))
            reports.append(self.ingest_document(document, force=force))
            if progress:
                progress(index, len(documents))
        return reports

    def ingest_upload(
        self,
        filename: str,
        data: bytes,
        *,
        title: str | None = None,
    ) -> IngestReport:
        safe = _SAFE_NAME.sub("_", Path(filename).name).strip("._")
        if not safe:
            safe = f"upload-{uuid.uuid4().hex[:8]}"
        suffix = Path(safe).suffix.lower()
        if suffix not in {".md", ".markdown", ".txt", ".html", ".htm", ".pdf"}:
            raise ValueError(f"不支持的文件类型: {suffix or '无后缀'}")
        if len(data) > self.settings.max_upload_mb * 1024 * 1024:
            raise ValueError(f"文件超过 {self.settings.max_upload_mb}MB 限制")

        upload_dir = self.settings.uploads_dir
        upload_dir.mkdir(parents=True, exist_ok=True)
        path = upload_dir / safe
        path.write_bytes(data)
        source = f"uploads/{safe}"
        try:
            return self.ingest_path(path, source=source, title=title)
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise
