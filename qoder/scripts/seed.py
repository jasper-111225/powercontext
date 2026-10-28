#!/usr/bin/env python
"""把 data/corpus 下的资料灌进 seekdb。

用法：
  python scripts/seed.py              # 增量：同一路径会覆盖
  python scripts/seed.py --reset      # 清空 documents/chunks 后重灌（保留问答历史）
  python scripts/seed.py --only '*.md'
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.embed import Embedder  # noqa: E402
from app.pipeline import corpus_files, ingest_path  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=str(ROOT / "data" / "corpus"))
    parser.add_argument("--only", default="*")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    settings.ensure_runtime()
    db.init_db()
    if args.reset:
        conn = db.connect()
        try:
            db.reset_corpus(conn)
        finally:
            conn.close()
    files = [p for p in corpus_files(Path(args.dir)) if p.match(args.only)]
    if not files:
        print(f"没有找到资料：{args.dir}/{args.only}", file=sys.stderr)
        return 1

    manifest_path = Path(args.dir) / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"seekdb {settings.db_host}:{settings.db_port}/{settings.db_name} · {len(files)} 篇资料")
    embedder = Embedder()
    started = time.time()
    total_chunks = 0
    for index, path in enumerate(files, start=1):
        t0 = time.time()
        print(f"[{index}/{len(files)}] {path.relative_to(Path(args.dir))}", flush=True)
        result = ingest_path(path, embedder, title=manifest.get(path.name))
        total_chunks += result["chunks"]
        print(
            f"    {result['kind']:8s} {result['chunks']:3d} 块  "
            f"{result['chars']:7d} 字符  {time.time() - t0:5.1f}s",
            flush=True,
        )

    conn = db.connect()
    try:
        stats = db.corpus_stats(conn)
    finally:
        conn.close()
    summary = {
        "files": len(files),
        "chunks_written": total_chunks,
        "corpus": stats,
        "elapsed_s": round(time.time() - started, 1),
        "embed_model": settings.embed_model,
        "db": f"{settings.db_host}:{settings.db_port}/{settings.db_name}",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
