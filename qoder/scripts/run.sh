#!/usr/bin/env bash
# 启动「据典」Web 服务（默认 http://127.0.0.1:8766）。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "还没有虚拟环境，先运行 scripts/setup.sh" >&2
  exit 1
fi

# 加载 .env（可选）
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

HOST="${JUDIAN_HOST:-127.0.0.1}"
PORT="${JUDIAN_PORT:-8766}"
echo "据典 → http://$HOST:$PORT"
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" --log-level info
