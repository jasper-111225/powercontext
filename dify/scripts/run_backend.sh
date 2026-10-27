#!/usr/bin/env bash
# 启动回声酒馆后端（默认 0.0.0.0:8765）。
# 用法： bash scripts/run_backend.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
[ -x "$PY" ] || { echo "先跑 bash scripts/setup.sh"; exit 1; }
exec "$PY" -m uvicorn backend.app:app --host "${ECHO_HOST:-0.0.0.0}" --port "${ECHO_PORT:-8765}"
