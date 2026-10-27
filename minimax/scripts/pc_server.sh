#!/usr/bin/env bash
# Start/stop/restart the local PowerContext Server used by the demo.
# Data lives in .runtime/powercontext-home so a restart keeps the saved state.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MINIMAX_DIR="$(cd "$HERE/.." && pwd)"
RUNTIME="$MINIMAX_DIR/.runtime"
ENV_FILE="$RUNTIME/powercontext.env"
PID_FILE="$RUNTIME/powercontext.pid"
DATA_DIR="$RUNTIME/powercontext-home"
LOG_FILE="$RUNTIME/powercontext-server.log"
PORT="${SDDCTX_POWERCONTEXT_PORT:-17429}"
TOKEN="${SDDCTX_POWERCONTEXT_TOKEN:-minimax-sdd-local-token}"

mkdir -p "$DATA_DIR"
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
    echo "PowerContext already running on :$PORT"
    return 0
  fi
  command -v powercontext >/dev/null || {
    echo "powercontext CLI not found. Install it with:" >&2
    echo "  uv tool install 'powercontext[cli,server]==1.4.0'" >&2
    exit 1
  }
  # setsid detaches the Server from this shell so it survives the calling script.
  POWERCONTEXT_HOME="$DATA_DIR" setsid nohup powercontext server run --env-file "$ENV_FILE" \
    > "$LOG_FILE" 2>&1 < /dev/null &
  echo $! > "$PID_FILE"
  for _ in $(seq 1 60); do
    if ready; then
      echo "PowerContext started on :$PORT (pid $(cat "$PID_FILE"))"
      return 0
    fi
    sleep 1
  done
  echo "PowerContext did not become ready; see $LOG_FILE" >&2
  tail -20 "$LOG_FILE" >&2 || true
  exit 1
}

stop() {
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    kill "$(cat "$PID_FILE")"
    for _ in $(seq 1 30); do
      ready || break
      sleep 1
    done
    echo "PowerContext stopped"
  else
    echo "PowerContext is not running (no live pid file)"
  fi
  rm -f "$PID_FILE"
}

status() {
  if ready; then echo "ready on :$PORT"; else echo "not ready"; return 1; fi
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  *) echo "usage: $0 {start|stop|restart|status}" >&2; exit 2 ;;
esac
