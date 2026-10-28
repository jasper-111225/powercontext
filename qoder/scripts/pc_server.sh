#!/usr/bin/env bash
# 启动/停止「据典」用的本地 PowerContext Server（SQLite 存储，数据在 .runtime）。
# 记忆写入后即使重启 Server 也还在，用来演示「越用越懂你」。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
RUNTIME="$ROOT/.runtime"
ENV_FILE="$RUNTIME/powercontext.env"
PID_FILE="$RUNTIME/powercontext.pid"
DATA_DIR="$RUNTIME/powercontext-home"
LOG_FILE="$RUNTIME/powercontext-server.log"
PORT="${JUDIAN_POWERCONTEXT_PORT:-17430}"
TOKEN="${JUDIAN_POWERCONTEXT_TOKEN:-judian-local-token}"

find_bin() {
  if [[ -n "${POWERCONTEXT_BIN:-}" && -x "$POWERCONTEXT_BIN" ]]; then echo "$POWERCONTEXT_BIN"; return; fi
  if command -v powercontext >/dev/null 2>&1; then command -v powercontext; return; fi
  for candidate in \
    "$ROOT/../../.toolchain/bin/powercontext" \
    "$HOME/.local/bin/powercontext"; do
    [[ -x "$candidate" ]] && { echo "$candidate"; return; }
  done
  echo ""
}

PC_BIN="$(find_bin)"
mkdir -p "$DATA_DIR" "$RUNTIME"

if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1
POWERCONTEXT_SERVER_HTTP_PORT=$PORT
POWERCONTEXT_SERVER_ACCESS_MODE=enforced
POWERCONTEXT_SERVER_AUTH_TOKEN=$TOKEN
POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false
POWERCONTEXT_SERVER_DATABASE_KIND=sqlite
EOF
fi

ready() {
  curl -sf -m 2 -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/health/ready" >/dev/null 2>&1
}

start() {
  if ready; then
    echo "PowerContext 已在 :$PORT 运行"
    return 0
  fi
  [[ -n "$PC_BIN" ]] || {
    echo "找不到 powercontext CLI。安装：" >&2
    echo "  uv tool install 'powercontext[cli,server]==1.4.0'" >&2
    exit 1
  }
  POWERCONTEXT_HOME="$DATA_DIR" setsid nohup "$PC_BIN" server run --env-file "$ENV_FILE" \
    > "$LOG_FILE" 2>&1 < /dev/null &
  echo $! > "$PID_FILE"
  for _ in $(seq 1 45); do
    if ready; then
      echo "PowerContext 已启动：http://127.0.0.1:$PORT（pid $(cat "$PID_FILE")）"
      return 0
    fi
    sleep 1
  done
  echo "PowerContext 未就绪，日志：" >&2
  tail -30 "$LOG_FILE" >&2 || true
  exit 1
}

stop() {
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    kill "$(cat "$PID_FILE")"
    for _ in $(seq 1 20); do ready || break; sleep 1; done
    echo "PowerContext 已停止"
  else
    echo "PowerContext 未在运行"
  fi
  rm -f "$PID_FILE"
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) if ready; then echo "ready :$PORT"; else echo "not ready"; exit 1; fi ;;
  *) echo "用法: $0 {start|stop|restart|status}" >&2; exit 2 ;;
esac
