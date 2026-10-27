#!/usr/bin/env bash
# Wipe the demo state so the evidence run starts from an empty environment:
# the seekdb schema, the PowerContext scope and its SQLite database.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MINIMAX_DIR="$(cd "$HERE/.." && pwd)"
PY="$MINIMAX_DIR/.venv/bin/python"

"$HERE/pc_server.sh" stop >/dev/null 2>&1 || true
rm -rf "$MINIMAX_DIR/.runtime/powercontext-home" "$MINIMAX_DIR/.runtime/scope.json"

"$PY" - <<'PY'
import os

import pymysql

database = os.environ.get("SDDCTX_DATABASE", "minimax_sdd")
conn = pymysql.connect(host=os.environ.get("SDDCTX_MYSQL_HOST", "127.0.0.1"),
                       port=int(os.environ.get("SDDCTX_MYSQL_PORT", "2881")),
                       user=os.environ.get("SDDCTX_MYSQL_USER", "root"),
                       password=os.environ.get("SDDCTX_MYSQL_PASSWORD", ""),
                       autocommit=True)
with conn.cursor() as cur:
    cur.execute(f"DROP DATABASE IF EXISTS `{database}`")
conn.close()
print(f"dropped seekdb database {database}")
PY

echo "environment reset (PowerContext data + seekdb schema)"
