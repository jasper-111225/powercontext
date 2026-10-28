#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
if [ ! -x ".venv/bin/python" ] && [ ! -x ".venv/Scripts/python.exe" ]; then
  echo "==> 创建虚拟环境 .venv"
  "$PYTHON" -m venv .venv
fi

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY=".venv/Scripts/python.exe"
fi

echo "==> 安装依赖"
"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install -e ".[dev]"

echo "==> 检查 seekdb"
if ! "$PY" - <<'PY'
import sys
from app.config import Settings
from app.db import SeekDB
settings = Settings.load()
ok, detail = SeekDB(settings).ping()
print(("OK   " if ok else "FAIL ") + detail)
raise SystemExit(0 if ok else 1)
PY
then
  echo "seekdb 不可用：请先启动 1.4.x 实例并监听 2881，或修改 .env 中的 JUDIAN_DB_*。" >&2
  exit 1
fi

echo "==> 检查向量模型"
if ! "$PY" - <<'PY'
import sys
from app.config import Settings
from app.embed import Embedder
settings = Settings.load()
ok, detail = Embedder(settings).available()
print(("OK   " if ok else "FAIL ") + detail)
raise SystemExit(0 if ok else 1)
PY
then
  echo "向量模型不可用：请执行 ollama pull qwen3-embedding:0.6b（首次加载可能需要十几秒）。" >&2
  exit 1
fi

echo "==> 建库并灌入语料"
"$PY" scripts/seed.py "$@"

cat <<'EOF'

准备完成。下一步：
  ./scripts/pc_server.sh start   # 启动 PowerContext（可选，未启动时记忆自动本地暂存）
  ./scripts/run.sh               # 打开 http://127.0.0.1:8766
EOF
