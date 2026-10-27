#!/usr/bin/env bash
# 一次性安装后端依赖并初始化 seekdb。
# 用法： bash scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
elif command -v uv >/dev/null 2>&1; then
  echo "== 用 uv 创建 .venv（Python 3.10+）"
  UV_CACHE_DIR=${UV_CACHE_DIR:-$PWD/.uv-cache} uv venv .venv --python 3.10
  PY=.venv/bin/python
else
  echo "== 用 venv 创建 .venv"
  python3 -m venv .venv
  PY=.venv/bin/python
fi

echo "== 安装依赖"
if command -v uv >/dev/null 2>&1; then
  UV_CACHE_DIR=${UV_CACHE_DIR:-$PWD/.uv-cache} uv pip install --python "$PY" -r backend/requirements.txt
else
  "$PY" -m pip install -r backend/requirements.txt
fi

echo "== 初始化 seekdb"
"$PY" scripts/init_db.py

echo
echo "完成。启动后端：bash scripts/run_backend.sh"
echo "可选自检：$PY scripts/check_dsl.py dsl/echo-tavern.chatflow.yml"
