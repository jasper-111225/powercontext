#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PY=".venv/Scripts/python.exe"
else
  echo "未找到 .venv，请先运行 ./scripts/setup.sh" >&2
  exit 1
fi

HOST="${JUDIAN_HOST:-127.0.0.1}"
PORT="${JUDIAN_PORT:-8766}"
RELOAD=""
if [ "${JUDIAN_RELOAD:-0}" = "1" ]; then
  RELOAD="--reload"
fi

echo "据典 · http://${HOST}:${PORT}"
exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT" $RELOAD
