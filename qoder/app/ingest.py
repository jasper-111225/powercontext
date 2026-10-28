"""文档解析与分块。

支持 Markdown / 纯文本 / HTML / PDF。分块目标不是「固定 512 token」，
而是**让一块仍然能被引用**：优先按标题与段落切，块内保留标题路径，
PDF 保留页码，这样答案里的 [n] 才能落回原文位置。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

TARGET_CHARS = 760
MAX_CHARS = 1050
OVERLAP_CHARS = 110

_CN_SENTENCE = re.compile(r"(?<=[。！？!?；;])")
_HEADING = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")


@dataclass
class Chunk:
    ordinal: int
    heading: str
    page: int | None
    text: str


@dataclass
class ParsedDoc:
    title: str
    kind: str
    source: str
    pages: int | None
    chars: int
    chunks: list[Chunk]


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_long(paragraph: str, limit: int) -> list[str]:
    """超长段落按句号切；再不行按字符硬切。"""
    if len(paragraph) <= limit:
        return [paragraph]
    sentences = [s for s in _CN_SENTENCE.split(paragraph) if s.strip()]
    pieces: list[str] = []
    buffer = ""
    for sentence in sentences:
        if len(buffer) + len(sentence) <= limit:
            buffer += sentence
        else:
            if buffer:
                pieces.append(buffer)
            if len(sentence) > limit:
                for i in range(0, len(sentence), limit):
                    pieces.append(sentence[i : i + limit])
                buffer = ""
            else:
                buffer = sentence
    if buffer:
        pieces.append(buffer)
    return pieces


def _pack(paragraphs: list[tuple[str, str, int | None]], *, target=TARGET_CHARS) -> list[Chunk]:
    """把 (heading, paragraph, page) 打包成块，同一标题内带少量重叠。"""
    chunks: list[Chunk] = []
    buffer = ""
    buffer_heading = ""
    buffer_page: int | None = None
    ordinal = 0

    def emit(*, overlap: bool = True) -> None:
        nonlocal buffer, ordinal
        text = buffer.strip()
        if len(text) >= 8:
            chunks.append(
                Chunk(ordinal=ordinal, heading=buffer_heading, page=buffer_page, text=text)
            )
            ordinal += 1
            buffer = text[-OVERLAP_CHARS:] + "\n" if overlap and len(text) > OVERLAP_CHARS else ""
        else:
            buffer = ""

    for heading, paragraph, page in paragraphs:
        for piece in _split_long(paragraph, MAX_CHARS):
            if heading != buffer_heading:
                if buffer.strip():
                    emit(overlap=False)
                buffer = ""
                buffer_heading, buffer_page = heading, page
            if len(buffer) + len(piece) + 2 > MAX_CHARS and buffer.strip():
                emit(overlap=True)
                buffer_page = page
            buffer += piece + "\n\n"
            if len(buffer) >= target:
                emit(overlap=True)
                buffer_page = page
    if buffer.strip():
        text = buffer.strip()
        if len(text) >= 8:
            chunks.append(
                Chunk(ordinal=ordinal, heading=buffer_heading, page=buffer_page, text=text)
            )
    return chunks


def chunk_markdown(text: str) -> tuple[str, list[Chunk]]:
    lines = text.split("\n")
    title = ""
    heading_stack: dict[int, str] = {}
    paragraphs: list[tuple[str, str, int | None]] = []
    buffer: list[str] = []
    current_heading = ""
    in_code = False

    def current_path() -> str:
        parts = [heading_stack[level] for level in sorted(heading_stack)]
        return " › ".join(parts)

    def flush_paragraph() -> None:
        nonlocal buffer
        body = "\n".join(buffer).strip()
        buffer = []
        if body:
            paragraphs.append((current_heading, body, None))

    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
        if not in_code:
            match = _HEADING.match(line)
            if match:
                flush_paragraph()
                level = len(match.group(1))
                heading_stack = {k: v for k, v in heading_stack.items() if k < level}
                heading_stack[level] = match.group(2).strip()
                if level == 1 and not title:
                    title = match.group(2).strip()
                current_heading = current_path()
                continue
        if not line.strip() and not in_code:
            flush_paragraph()
        else:
            buffer.append(line)
    flush_paragraph()

    if not paragraphs:
        paragraphs = [(title, text, None)]
    return title, _pack(paragraphs)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if not self.skip:
            self.parts.append(data)


def chunk_pdf(path: Path) -> tuple[str, int, list[Chunk]]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    paragraphs: list[tuple[str, str, int | None]] = []
    for page_no, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - 单页损坏不影响整篇
            text = ""
        text = _clean(text)
        if text:
            paragraphs.append((f"第 {page_no} 页", text, page_no))
    title = ""
    try:
        meta = reader.metadata or {}
        title = (meta.get("/Title") or "").strip()
    except Exception:  # noqa: BLE001
        title = ""
    title = title or path.stem
    return title, len(reader.pages), _pack(paragraphs)


def chunk_html(text: str) -> tuple[str, list[Chunk]]:
    parser = _TextExtractor()
    try:
        parser.feed(text)
    except Exception:  # noqa: BLE001
        pass
    plain = _clean("".join(parser.parts))
    title = parser.title.strip()
    paragraphs = [(title, p, None) for p in plain.split("\n\n") if p.strip()]
    return title, _pack(paragraphs)


def chunk_text(text: str, title: str = "") -> list[Chunk]:
    paragraphs = []
    for block in _clean(text).split("\n\n"):
        block = block.strip()
        if block:
            paragraphs.append((title, block, None))
    return _pack(paragraphs)


def parse_file(path: Path, *, title: str | None = None) -> ParsedDoc:
    suffix = path.suffix.lower()
    raw = path.read_text(encoding="utf-8", errors="replace") if suffix not in {".pdf"} else ""
    if suffix == ".pdf":
        doc_title, pages, chunks = chunk_pdf(path)
        chars = sum(len(c.text) for c in chunks)
        kind = "pdf"
    elif suffix in {".html", ".htm"}:
        doc_title, chunks = chunk_html(raw)
        pages, chars, kind = None, len(raw), "html"
    elif suffix in {".md", ".markdown"}:
        doc_title, chunks = chunk_markdown(raw)
        pages, chars, kind = None, len(raw), "markdown"
    else:
        doc_title, chunks = (title or path.stem), chunk_text(raw, title or path.stem)
        pages, chars, kind = None, len(raw), "text"
    doc_title = (title or doc_title or path.stem).strip()[:200]
    return ParsedDoc(
        title=doc_title,
        kind=kind,
        source=str(path),
        pages=pages,
        chars=chars,
        chunks=chunks,
    )


def checksum(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()
