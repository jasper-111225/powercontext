"""Parse a project into chunked, requirement-linked engineering material.

Chunking rules
--------------
* ``specs/**.md``  -> one chunk per ``##``/``###``/``####`` section, plus the
  document introduction. Requirement ids (``REQ-001.2``) are read from the text.
* ``src/**.py``    -> one chunk per top-level function and per method; requirement
  ids are read from ``@req`` traceability comments.
* ``tests/**.py``  -> one chunk per ``test_*`` function; requirement ids are read
  from the ``@req`` marker in the docstring.

The parser is deliberately dependency-free (``ast`` + ``re``) so a clean machine
can re-import after editing a specification and see the new content.
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Any, Iterable

REQ_RE = re.compile(r"REQ-\d{3}(?:\.\d+)?")
CHANGE_RE = re.compile(r"变更[:：]\s*(新增|既有)")
VERSION_RE = re.compile(r"版本[:：]\s*(\d+)")
HEADING_RE = re.compile(r"^(#{2,4})\s+(.*\S)\s*$")
REQ_MARKER_RE = re.compile(r"@req\s+([^\n]*)")

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv", "node_modules"}


def discover(project_root: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for kind, sub in (("spec", "specs"), ("code", "src"), ("test", "tests")):
        base = project_root / sub
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
                continue
            if kind == "spec" and path.suffix != ".md":
                continue
            if kind != "spec" and path.suffix != ".py":
                continue
            found.append((kind, path))
    return found


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def req_ids(text: str) -> list[str]:
    return sorted(set(REQ_RE.findall(text)))


def _extend_over_comments(lines: list[str], start: int) -> int:
    """Include directly preceding ``#`` comment lines so ``@req`` markers are kept."""
    while start > 1 and lines[start - 2].lstrip().startswith("#"):
        start -= 1
    return start


def _chunk(symbol: str, title: str, lines: list[str], start: int, end: int) -> dict[str, Any]:
    content = "\n".join(lines[start - 1 : end])
    return {
        "symbol": symbol[:191],
        "title": title[:255],
        "line_start": start,
        "line_end": end,
        "content": content,
        "req_ids": req_ids(content),
        "content_hash": sha256(content),
    }


def _spec_document(path: Path) -> tuple[str, int, str, list[dict[str, Any]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    title = path.stem
    headings: list[tuple[int, int, str]] = []  # (line_no, level, text)
    for number, line in enumerate(lines, start=1):
        if line.startswith("# "):
            title = line[2:].strip()
        match = HEADING_RE.match(line)
        if match:
            headings.append((number, len(match.group(1)), match.group(2).strip()))
    version_match = VERSION_RE.search("\n".join(lines))
    version = int(version_match.group(1)) if version_match else 1
    change_match = CHANGE_RE.search("\n".join(lines))
    change_kind = "new" if change_match and change_match.group(1) == "新增" else "existing"

    chunks: list[dict[str, Any]] = []
    first_heading = headings[0][0] if headings else len(lines) + 1
    if first_heading > 1:
        intro = _chunk(title, title, lines, 1, first_heading - 1)
        if intro["content"].strip():
            chunks.append(intro)
    for index, (line_no, _level, text) in enumerate(headings):
        end = headings[index + 1][0] - 1 if index + 1 < len(headings) else len(lines)
        if end < line_no:
            continue
        chunk = _chunk(text, text, lines, line_no, end)
        own_ids = REQ_RE.findall(text)
        if own_ids:
            chunk["req_ids"] = sorted(set(own_ids))
        chunks.append(chunk)
    return title, version, change_kind, chunks


def _python_chunks(path: Path) -> list[dict[str, Any]]:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [_chunk(path.stem, path.stem, lines, 1, len(lines))]

    chunks: list[dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            if node.decorator_list:
                start = min(item.lineno for item in node.decorator_list)
            start = _extend_over_comments(lines, start)
            chunks.append(_chunk(node.name, node.name, lines, start, node.end_lineno))
        elif isinstance(node, ast.ClassDef):
            methods = [
                item for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            if methods:
                first = methods[0].lineno
                if methods[0].decorator_list:
                    first = min(item.lineno for item in methods[0].decorator_list)
                class_start = _extend_over_comments(lines, node.lineno)
                if first - class_start > 1:
                    chunks.append(_chunk(node.name, node.name, lines, class_start, first - 1))
            else:
                chunks.append(_chunk(node.name, node.name, lines, node.lineno, node.end_lineno))
            for method in methods:
                start = method.lineno
                if method.decorator_list:
                    start = min(item.lineno for item in method.decorator_list)
                start = _extend_over_comments(lines, start)
                chunks.append(
                    _chunk(f"{node.name}.{method.name}", f"{node.name}.{method.name}",
                           lines, start, method.end_lineno)
                )
    return chunks


def _test_chunks(path: Path) -> list[dict[str, Any]]:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    chunks: list[dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        start = node.lineno
        if node.decorator_list:
            start = min(item.lineno for item in node.decorator_list)
        content = "\n".join(lines[start - 1 : node.end_lineno])
        marked = REQ_MARKER_RE.findall(content)
        ids = sorted({req for group in marked for req in REQ_RE.findall(group)})
        chunk = _chunk(node.name, (ast.get_docstring(node) or node.name).splitlines()[0],
                       lines, start, node.end_lineno)
        chunk["req_ids"] = ids
        chunks.append(chunk)
    return chunks


def parse_document(kind: str, path: Path) -> tuple[str, int, str, list[dict[str, Any]]]:
    if kind == "spec":
        return _spec_document(path)
    if kind == "code":
        return path.stem, 1, "existing", _python_chunks(path)
    return path.stem, 1, "existing", _test_chunks(path)


def import_project(db: Any, project_root: Path, project_name: str) -> dict[str, Any]:
    """Chunk every material of ``project_root`` and replace it in seekdb."""
    project_root = project_root.resolve()
    documents = 0
    chunk_total = 0
    per_kind: dict[str, int] = {"spec": 0, "code": 0, "test": 0}
    req_total: set[str] = set()

    for kind, path in discover(project_root):
        rel = path.relative_to(project_root).as_posix()
        title, version, change_kind, chunks = parse_document(kind, path)
        digest = sha256(path.read_text(encoding="utf-8"))
        doc_id = db.upsert_document(
            project_name, kind, rel, title, version, digest, change_kind
        )
        count = db.replace_chunks(doc_id, project_name, kind, rel, chunks)
        documents += 1
        chunk_total += count
        per_kind[kind] += count
        for chunk in chunks:
            req_total.update(chunk["req_ids"])

    return {
        "project": project_name,
        "root": str(project_root),
        "documents": documents,
        "chunks": chunk_total,
        "per_kind": per_kind,
        "requirements": sorted(req_total),
    }
