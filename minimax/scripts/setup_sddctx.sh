#!/usr/bin/env bash
# Install the Python environment used by sddctx and the demo project.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MINIMAX_DIR="$(cd "$HERE/.." && pwd)"
cd "$MINIMAX_DIR"

# 使用 uv 的默认缓存目录；如已显式设置 UV_CACHE_DIR 则沿用

if [[ ! -x .venv/bin/python ]]; then
  uv venv --python 3.12 .venv
fi
uv pip install --python .venv/bin/python -r requirements.txt

echo "python: $(.venv/bin/python -V)"
echo "deps: $(.venv/bin/python -c 'import pymysql, pytest; print("pymysql", pymysql.__version__, "| pytest", pytest.__version__)')"

if ! command -v powercontext >/dev/null; then
  echo
  echo "警告：未找到 PowerContext CLI。运行演示前请安装："
  echo "  uv tool install 'powercontext[cli,server]==1.4.0'"
  exit 1
fi
echo "powercontext: $(powercontext --version 2>/dev/null || echo unknown)"
