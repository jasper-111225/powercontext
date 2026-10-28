from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

TARGET_CHARS = 760
MAX_CHARS = 1050
OVERLAP_CHARS = 110

_SUPPORTED = {".md", ".markdown", ".txt", ".html", ".htm", ".pdf"}
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SENTENCE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;\n]?")


@dataclass
class ChunkDraft:
    text: str
    heading: str = ""
    page: int | None = None
    ordinal: int = 0


@dataclass
class ParsedDocument:
    title: str
    kind: str
    source: str
    text: str
    chunks: list[ChunkDraft] = field(default_factory=list)
    pages: int | None = None
    checksum: str = ""

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)


def checksum_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_frontmatter(text: str) -> tuple[str, dict[str, str]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        return text, {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return text, {}
    raw = text[4:end]
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip("\"'")
    return text[end + 5 :], meta


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", "", value).strip().lower()


def _markdown_sections(text: str, doc_title: str) -> list[tuple[str, str]]:
    body, _ = strip_frontmatter(text)
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    content: list[str] = []
    in_fence = False
    fence_marker = ""

    def flush() -> None:
        raw = "\n".join(content).strip("\n")
        if raw.strip():
            heading = " › ".join(title for _, title in stack)
            sections.append((heading, raw))
        content.clear()

    for line in body.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            content.append(line)
            continue
        match = None if in_fence else _HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            if level == 1 and _normalize_title(title) == _normalize_title(doc_title):
                stack = []
            else:
                stack = [item for item in stack if item[0] < level]
                stack.append((level, title))
            continue
        content.append(line)
    flush()
    return sections


def _split_prose(block: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for match in _SENTENCE.finditer(block):
        sentence = match.group(0)
        if not sentence:
            continue
        if current and len(current) + len(sentence) > max_chars:
            pieces.append(current.strip())
            current = sentence
        else:
            current += sentence
        while len(current) > max_chars:
            pieces.append(current[:max_chars].strip())
            current = current[max_chars:]
    if current.strip():
        pieces.append(current.strip())
    return [piece for piece in pieces if piece]


def _units(content: str, max_chars: int) -> list[str]:
    units: list[str] = []
    paragraph: list[str] = []
    fence: list[str] = []
    in_fence = False
    fence_marker = ""

    def flush_paragraph() -> None:
        block = "\n".join(paragraph).strip()
        paragraph.clear()
        if not block:
            return
        if len(block) <= max_chars:
            units.append(block)
        else:
            units.extend(_split_long_block(block, max_chars))

    def flush_fence() -> None:
        block = "\n".join(fence).strip("\n")
        fence.clear()
        if not block:
            return
        if len(block) <= max_chars:
            units.append(block)
        else:
            # Keep all code text, even when a single fence is too large.
            lines = block.split("\n")
            buffer: list[str] = []
            size = 0
            for line in lines:
                if buffer and size + len(line) + 1 > max_chars:
                    units.append("\n".join(buffer))
                    buffer = []
                    size = 0
                buffer.append(line)
                size += len(line) + 1
            if buffer:
                units.append("\n".join(buffer))

    for line in content.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                flush_paragraph()
                in_fence = True
                fence_marker = marker
                fence.append(line)
            else:
                fence.append(line)
                if marker == fence_marker:
                    in_fence = False
                    fence_marker = ""
                    flush_fence()
            continue
        if in_fence:
            fence.append(line)
            continue
        if line.strip():
            paragraph.append(line)
        else:
            flush_paragraph()
    flush_paragraph()
    if fence:
        flush_fence()
    return units


def _split_long_block(block: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    for paragraph in re.split(r"\n\s*\n", block):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
        else:
            pieces.extend(_split_prose(paragraph, max_chars))
    return pieces


def pack_section(
    content: str,
    target: int = TARGET_CHARS,
    max_chars: int = MAX_CHARS,
    overlap: int = OVERLAP_CHARS,
) -> list[str]:
    units = _units(content, max_chars)
    chunks: list[str] = []
    buffer = ""

    for unit in units:
        if buffer and len(buffer) + 1 + len(unit) > target:
            chunks.append(buffer)
            tail = buffer[-overlap:] if overlap else ""
            candidate = f"{tail}\n{unit}" if tail else unit
            buffer = candidate if len(candidate) <= max_chars else unit
        else:
            candidate = f"{buffer}\n{unit}" if buffer else unit
            if len(candidate) > max_chars and buffer:
                chunks.append(buffer)
                buffer = unit
            else:
                buffer = candidate

    if buffer.strip():
        chunks.append(buffer)

    deduped: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if chunk and (not deduped or chunk != deduped[-1]):
            deduped.append(chunk)
    return deduped


def chunk_markdown(
    text: str,
    doc_title: str,
    target: int = TARGET_CHARS,
    max_chars: int = MAX_CHARS,
    overlap: int = OVERLAP_CHARS,
) -> tuple[str, list[ChunkDraft]]:
    body, meta = strip_frontmatter(text)
    title = meta.get("title") or doc_title
    sections = _markdown_sections(body, title)
    chunks: list[ChunkDraft] = []
    for heading, content in sections:
        for piece in pack_section(content, target, max_chars, overlap):
            chunks.append(ChunkDraft(text=piece, heading=heading))
    for ordinal, chunk in enumerate(chunks):
        chunk.ordinal = ordinal
    return title, chunks


class _HTMLText(HTMLParser):
    _BLOCK = {"p", "div", "section", "article", "li", "tr", "blockquote"}
    _HEAD = {"h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._heading_level = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self._HEAD:
            self._heading_level = int(tag[1])
            self.parts.append("\n" + "#" * self._heading_level + " ")
        elif tag == "pre":
            self.parts.append("\n```\n")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in self._HEAD:
            self._heading_level = 0
            self.parts.append("\n")
        elif tag == "pre":
            self.parts.append("\n```\n")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data
            return
        self.parts.append(data)

    @property
    def text(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def parse_markdown(path: Path, source: str, title: str | None = None) -> ParsedDocument:
    data = path.read_bytes()
    raw = data.decode("utf-8", errors="replace")
    doc_title, chunks = chunk_markdown(raw, title or path.stem)
    body, _ = strip_frontmatter(raw)
    return ParsedDocument(
        title=title or doc_title,
        kind="markdown",
        source=source,
        text=body.strip(),
        chunks=chunks,
        checksum=checksum_bytes(data),
    )


def parse_text(path: Path, source: str, title: str | None = None) -> ParsedDocument:
    data = path.read_bytes()
    raw = data.decode("utf-8", errors="replace")
    chunks = [
        ChunkDraft(text=piece, heading="")
        for piece in pack_section(raw.strip(), TARGET_CHARS, MAX_CHARS, OVERLAP_CHARS)
    ]
    for ordinal, chunk in enumerate(chunks):
        chunk.ordinal = ordinal
    return ParsedDocument(
        title=title or path.stem,
        kind="text",
        source=source,
        text=raw.strip(),
        chunks=chunks,
        checksum=checksum_bytes(data),
    )


def parse_html(path: Path, source: str, title: str | None = None) -> ParsedDocument:
    data = path.read_bytes()
    raw = data.decode("utf-8", errors="replace")
    parser = _HTMLText()
    parser.feed(raw)
    doc_title, chunks = chunk_markdown(parser.text, title or parser.title or path.stem)
    return ParsedDocument(
        title=title or parser.title.strip() or doc_title,
        kind="html",
        source=source,
        text=parser.text,
        chunks=chunks,
        checksum=checksum_bytes(data),
    )


def parse_pdf(path: Path, source: str, title: str | None = None) -> ParsedDocument:
    from pypdf import PdfReader

    data = path.read_bytes()
    reader = PdfReader(path)
    chunks: list[ChunkDraft] = []
    texts: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        if not page_text:
            continue
        texts.append(page_text)
        for piece in pack_section(page_text, TARGET_CHARS, MAX_CHARS, OVERLAP_CHARS):
            chunks.append(ChunkDraft(text=piece, heading="", page=page_number))
    for ordinal, chunk in enumerate(chunks):
        chunk.ordinal = ordinal
    return ParsedDocument(
        title=title or path.stem,
        kind="pdf",
        source=source,
        text="\n\n".join(texts),
        chunks=chunks,
        pages=len(reader.pages),
        checksum=checksum_bytes(data),
    )


def parse_document(path: Path, source: str, title: str | None = None) -> ParsedDocument:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return parse_markdown(path, source, title)
    if suffix in {".txt"}:
        return parse_text(path, source, title)
    if suffix in {".html", ".htm"}:
        return parse_html(path, source, title)
    if suffix == ".pdf":
        return parse_pdf(path, source, title)
    raise ValueError(f"不支持的文件类型: {suffix}")


def read_manifest(corpus_dir: Path) -> dict[str, str]:
    path = corpus_dir / "manifest.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def corpus_files(corpus_dir: Path, only: str | None = None) -> list[Path]:
    files = [
        path
        for path in sorted(corpus_dir.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in _SUPPORTED
        and path.name not in {"README.md", "SOURCES.md", "manifest.json"}
    ]
    if only:
        from fnmatch import fnmatch

        files = [path for path in files if fnmatch(path.name, only)]
    return files


def title_for(path: Path, corpus_dir: Path, manifest: dict[str, str]) -> str | None:
    relative = path.relative_to(corpus_dir).as_posix()
    return manifest.get(relative) or manifest.get(path.name)


def parse_corpus(
    corpus_dir: Path, only: str | None = None, limit: int | None = None
) -> list[ParsedDocument]:
    manifest = read_manifest(corpus_dir)
    documents: list[ParsedDocument] = []
    for path in corpus_files(corpus_dir, only):
        if limit is not None and len(documents) >= limit:
            break
        source = path.relative_to(corpus_dir).as_posix()
        documents.append(parse_document(path, source, title_for(path, corpus_dir, manifest)))
    return documents


def iter_sources(documents: Iterable[ParsedDocument]) -> Iterable[tuple[ParsedDocument, ChunkDraft]]:
    for document in documents:
        for chunk in document.chunks:
            yield document, chunk
