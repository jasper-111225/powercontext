#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
if [ ! -x ".venv/bin/python" ] && [ ! -x ".venv/Scripts/python.exe" ]; then
  echo "==> 创建虚拟环境 .venv"
  created=0
  # 优先 uv：系统 Python 缺少 ensurepip 时 python3 -m venv 会直接失败。
  if command -v uv >/dev/null 2>&1; then
    if uv venv --python 3.12 .venv 2>/dev/null || uv venv .venv; then
      created=1
    fi
  fi
  if [ "$created" -eq 0 ]; then
    for candidate in python3.12 python3.11 "$PYTHON"; do
      if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -m venv .venv 2>/tmp/judian-venv.err; then
          created=1
          break
        fi
        echo "   $candidate 创建 venv 失败：$(tail -1 /tmp/judian-venv.err 2>/dev/null || true)" >&2
      fi
    done
  fi
  if [ "$created" -eq 0 ]; then
    cat >&2 <<'EOF'
无法创建 .venv：当前 Python 缺少 venv/ensurepip。
请任选一种后重试：
  sudo apt install python3-venv
  # 或安装 uv 后运行：
  uv venv --python 3.12 .venv
  # 或指定可用 Python：
  PYTHON=/path/to/python3.12 ./scripts/setup.sh
EOF
    exit 1
  fi
fi

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY=".venv/Scripts/python.exe"
fi

echo "==> 安装依赖"
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  echo "    venv 里没有 pip，尝试 ensurepip"
  "$PY" -m ensurepip --upgrade >/dev/null 2>&1 || {
    echo "无法安装 pip。请删除 .venv 后重试，或安装 python3-venv / python3-pip。" >&2
    exit 1
  }
fi
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
