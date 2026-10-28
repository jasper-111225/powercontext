#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$ROOT/.runtime"
HOME_DIR="$RUNTIME/powercontext-home"
LOG="$RUNTIME/powercontext-server.log"
PID_FILE="$RUNTIME/powercontext.pid"

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

PORT="${JUDIAN_POWERCONTEXT_PORT:-17430}"
URL="${JUDIAN_POWERCONTEXT_URL:-http://127.0.0.1:$PORT}"

find_bin() {
  if [ -n "${POWERCONTEXT_BIN:-}" ] && [ -x "$POWERCONTEXT_BIN" ]; then
    printf '%s\n' "$POWERCONTEXT_BIN"
    return 0
  fi
  if command -v powercontext >/dev/null 2>&1; then
    command -v powercontext
    return 0
  fi
  for candidate in \
    "$ROOT/../../.toolchain/bin/powercontext" \
    "$HOME/.local/bin/powercontext" \
    "$HOME/.cargo/bin/powercontext"; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

is_running() {
  [ -f "$PID_FILE" ] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" >/dev/null 2>&1
}

health() {
  ROOT="$ROOT" URL="$URL" "${PYTHON_BIN:-python3}" - <<'PY' >/dev/null 2>&1
import os, urllib.request
url = os.environ["URL"].rstrip("/")
for path in ("/v1/health", "/health", "/v1/scopes"):
    try:
        with urllib.request.urlopen(url + path, timeout=2) as response:
            if response.status < 500:
                raise SystemExit(0)
    except Exception:
        continue
raise SystemExit(1)
PY
}

choose_python() {
  if [ -x "$ROOT/.venv/bin/python" ]; then
    printf '%s\n' "$ROOT/.venv/bin/python"
  elif [ -x "$ROOT/.venv/Scripts/python.exe" ]; then
    printf '%s\n' "$ROOT/.venv/Scripts/python.exe"
  else
    printf '%s\n' "python3"
  fi
}
PYTHON_BIN="$(choose_python)"
export PYTHON_BIN

start() {
  if is_running; then
    echo "PowerContext 已在运行（pid $(cat "$PID_FILE")，$URL）"
    return 0
  fi
  local bin
  if ! bin="$(find_bin)"; then
    cat >&2 <<'EOF'
未找到 powercontext 可执行文件。请先安装：
  uv tool install "powercontext[cli,server]==1.4.0"
或：
  python -m pip install "powercontext[cli,server]==1.4.0"
也可以用 POWERCONTEXT_BIN=/path/to/powercontext 指定。
EOF
    return 1
  fi
  mkdir -p "$RUNTIME" "$HOME_DIR"
  export POWERCONTEXT_HOME="$HOME_DIR"
  export XDG_DATA_HOME="$HOME_DIR"

  local -a extra=()
  if [ -n "${POWERCONTEXT_ARGS:-}" ]; then
    # shellcheck disable=SC2206
    extra=(${POWERCONTEXT_ARGS})
  fi

  local -a args
  if [ "${#extra[@]}" -gt 0 ]; then
    args=("${extra[@]}")
  elif "$bin" server run --help >/dev/null 2>&1; then
    # PowerContext 1.4: `server` is a command group; the long-running
    # process lives under `server run`.
    args=(server run --host 127.0.0.1 --port "$PORT")
    if "$bin" server run --help 2>&1 | grep -q -- "--data-dir"; then
      args+=(--data-dir "$HOME_DIR")
    fi
  elif "$bin" server --help >/dev/null 2>&1; then
    args=(server --host 127.0.0.1 --port "$PORT")
    if "$bin" server --help 2>&1 | grep -q -- "--data-dir"; then
      args+=(--data-dir "$HOME_DIR")
    fi
  elif "$bin" serve --help >/dev/null 2>&1; then
    args=(serve --host 127.0.0.1 --port "$PORT")
  else
    args=(--host 127.0.0.1 --port "$PORT")
  fi

  echo "==> 启动 PowerContext：$bin ${args[*]}"
  nohup "$bin" "${args[@]}" >>"$LOG" 2>&1 &
  local pid=$!
  printf '%s\n' "$pid" >"$PID_FILE"

  for _ in $(seq 1 30); do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      echo "PowerContext 启动失败，最近日志：" >&2
      tail -n 30 "$LOG" >&2 || true
      rm -f "$PID_FILE"
      return 1
    fi
    if health; then
      echo "PowerContext 已就绪：$URL（pid $pid）"
      echo "日志：$LOG"
      return 0
    fi
    sleep 1
  done

  echo "PowerContext 进程已启动（pid $pid），但健康检查未通过。最近日志：" >&2
  tail -n 30 "$LOG" >&2 || true
  return 1
}

stop() {
  if ! is_running; then
    echo "PowerContext 未运行"
    rm -f "$PID_FILE"
    return 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  echo "==> 停止 PowerContext（pid $pid）"
  kill "$pid" >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      rm -f "$PID_FILE"
      echo "已停止"
      return 0
    fi
    sleep 0.5
  done
  echo "进程未在 10 秒内退出，发送 TERM 后仍存活；请手动检查 $LOG" >&2
  return 1
}

status() {
  if is_running; then
    local pid
    pid="$(cat "$PID_FILE")"
    if health; then
      echo "PowerContext 运行中：pid $pid，$URL，健康检查 OK"
    else
      echo "PowerContext 进程存在：pid $pid，但健康检查失败（$URL）"
    fi
    echo "日志：$LOG"
    return 0
  fi
  echo "PowerContext 未运行（$URL）"
  if [ -f "$LOG" ]; then
    echo "最近日志："
    tail -n 5 "$LOG" || true
  fi
  return 1
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart)
    stop || true
    sleep 1
    start
    ;;
  status) status ;;
  *)
    echo "用法：$0 {start|stop|restart|status}" >&2
    exit 2
    ;;
esac
