#!/usr/bin/env python3
"""把 data/corpus 解析、向量化并写入 seekdb。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.db import DBUnavailable, SeekDB  # noqa: E402
from app.embed import Embedder, EmbeddingError  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def main() -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="据典语料灌库")
    parser.add_argument("--reset", action="store_true", help="清空资料与片段后重灌（保留问答与笔记）")
    parser.add_argument("--only", metavar="GLOB", help="只灌匹配的文件名，例如 'build-*.md'")
    parser.add_argument("--limit", type=int, default=None, help="最多灌入 N 篇")
    parser.add_argument("--force", action="store_true", help="忽略 checksum，强制重新向量化")
    args = parser.parse_args()

    settings = Settings.load()
    db = SeekDB(settings)
    ok, detail = db.ping()
    if not ok:
        print(f"[FAIL] seekdb 连接失败：{detail}", file=sys.stderr)
        print("       请确认 127.0.0.1:2881 在运行，并检查 .env 中的 JUDIAN_DB_*。", file=sys.stderr)
        return 2

    try:
        db.ensure_database()
        db.ensure_schema()
    except DBUnavailable as exc:
        print(f"[FAIL] 建库/建表失败：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[FAIL] 建表失败：{exc}", file=sys.stderr)
        print("       请确认 seekdb 支持 VECTOR、FULLTEXT ... WITH PARSER ik 与 cosine_distance。", file=sys.stderr)
        return 2

    pipeline = Pipeline(settings, db=db, embedder=Embedder(settings))
    total = {"documents": 0, "chunks": 0, "chars": 0, "skipped": 0}

    def progress(index: int, count: int) -> None:
        if count:
            print(f"\r      语料进度 {index}/{count}", end="", flush=True)

    try:
        reports = pipeline.ingest_corpus(
            only=args.only,
            limit=args.limit,
            reset=args.reset,
            force=args.force,
            progress=progress,
        )
    except EmbeddingError as exc:
        print(f"\n[FAIL] 向量模型不可用：{exc}", file=sys.stderr)
        print("       请先 ollama pull qwen3-embedding:0.6b，再重试。", file=sys.stderr)
        return 3
    except DBUnavailable as exc:
        print(f"\n[FAIL] seekdb 写入失败：{exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1

    print()
    for report in reports:
        state = "跳过" if report.skipped else ("覆盖" if report.replaced else "新增")
        total["documents"] += 1
        total["chunks"] += report.chunks
        total["chars"] += report.chars
        total["skipped"] += 1 if report.skipped else 0
        print(
            f"[{state}] {report.title}  "
            f"{report.chunks} 片段 / {report.chars} 字符  ({report.source})"
        )

    stats = db.stats()
    print()
    print(
        f"完成：{stats['documents']} 篇资料，{stats['chunks']} 个片段，"
        f"{stats['chars']} 字符，平均 {stats['avg_chunk_chars']} 字符/片段。"
    )
    if total["skipped"]:
        print(f"其中 {total['skipped']} 篇 checksum 未变，已跳过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
