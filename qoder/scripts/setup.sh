#!/usr/bin/env bash
# 一次性准备：虚拟环境 + 依赖 + 建库 + 灌入示例语料。
# 前置：seekdb 在 127.0.0.1:2881、Ollama 在 127.0.0.1:11434 且已 pull 向量模型。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

command -v uv >/dev/null || {
  echo "需要 uv（https://docs.astral.sh/uv/）。也可以手动 python -m venv .venv && pip install -e ." >&2
  exit 1
}

if [[ ! -d .venv ]]; then
  uv venv --python 3.12 .venv
fi
# 复用工作区缓存时更快：UV_CACHE_DIR=/path/to/.uv-cache
uv pip install -e ".[dev]"

mkdir -p .runtime data/uploads

echo "检查 seekdb 与向量模型……"
.venv/bin/python - <<'EOF'
import sys
from app.config import settings
try:
    from app import db
    conn = db.connect(with_db=False)
    with conn.cursor() as cur:
        cur.execute("SELECT VERSION()")
        print("  seekdb:", cur.fetchone()["VERSION()"])
    conn.close()
except Exception as exc:
    print("  seekdb 连接失败：", exc, file=sys.stderr)
    sys.exit(1)
try:
    import httpx
    r = httpx.post(f"{settings.embed_base}/embeddings", json={"model": settings.embed_model, "input": ["ping"]}, timeout=120)
    r.raise_for_status()
    print("  向量模型:", settings.embed_model, "dim", len(r.json()["data"][0]["embedding"]))
except Exception as exc:
    print("  向量模型不可用：", exc, file=sys.stderr)
    sys.exit(1)
EOF

echo
echo "灌入示例语料（首次需要逐块向量化，约 10–20 分钟）……"
.venv/bin/python scripts/seed.py "$@"

cat <<'EOF'

准备完成。下一步：
  1. scripts/pc_server.sh start      # 启动 PowerContext（记忆）
  2. scripts/run.sh                  # 打开 http://127.0.0.1:8766
EOF
