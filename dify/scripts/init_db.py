#!/usr/bin/env python3
"""初始化 seekdb 库表与 NPC 种子数据，并做一次连通性检查。

用法：
  .venv/bin/python scripts/init_db.py            # 幂等建表 + 灌 NPC
  .venv/bin/python scripts/init_db.py --reset    # 删库重建（会清空所有玩家数据！）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import db  # noqa: E402
from backend.config import SETTINGS  # noqa: E402
from backend.embed import Embedder, EmbeddingError  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="删库重建（危险）")
    args = ap.parse_args()

    print(f"seekdb: {SETTINGS.db_host}:{SETTINGS.db_port} / {SETTINGS.db_name}")
    print(f"embedding: {SETTINGS.embed_base_url} / {SETTINGS.embed_model} ({SETTINGS.embed_dim}d)")
    db.init_schema(SETTINGS, reset=args.reset)
    print("✓ 建表完成（players / npcs / npc_state / rumors / memories）")

    conn = db.connect(SETTINGS)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, initial_trust FROM npcs ORDER BY id")
            npcs = cur.fetchall()
        print("✓ NPC 种子：" + ", ".join(f"{r['name']}({r['initial_trust']})" for r in npcs))
        with conn.cursor() as cur:
            cur.execute("SHOW INDEX FROM rumors")
            indexes = cur.fetchall()
        kinds = sorted({f"{r['Key_name']}/{r['Index_type']}" for r in indexes})
        print("✓ rumors 索引：" + ", ".join(kinds))
    finally:
        conn.close()

    try:
        embedder = Embedder(SETTINGS)
        try:
            vector = embedder.embed_one("回声酒馆")
        finally:
            embedder.close()
        print(f"✓ 向量化可用：dim={len(vector)}，样例前 3 维={[round(x, 3) for x in vector[:3]]}")
    except EmbeddingError as exc:
        print(f"✗ 向量化不可用：{exc}")
        print("  本机 Ollama 示例：ollama pull qwen3-embedding:0.6b")
        return 1
    print("\n下一步：bash scripts/run_backend.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
