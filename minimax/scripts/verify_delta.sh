#!/usr/bin/env bash
# Prove the change is effective: run the same target tests at the base commit
# (they must fail) and at HEAD (they must pass), with the regression suite green
# in both trees.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MINIMAX_DIR="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(git -C "$MINIMAX_DIR" rev-parse --show-toplevel)"
PY="$MINIMAX_DIR/.venv/bin/python"
LOG="$MINIMAX_DIR/evidence/logs"
BASE_REF="${1:-HEAD^}"
WT="$MINIMAX_DIR/.runtime/base-worktree"
mkdir -p "$LOG"

git -C "$REPO_ROOT" worktree prune
rm -rf "$WT"
git -C "$REPO_ROOT" worktree add --detach "$WT" "$BASE_REF" > /dev/null
cleanup() { git -C "$REPO_ROOT" worktree remove --force "$WT" > /dev/null 2>&1 || true; }
trap cleanup EXIT

run_suite() { # <project_dir> <test_path> <log_file>
  ( cd "$1" && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PY" -m pytest -q "$2" ) > "$3" 2>&1
  echo $?
}

echo "base commit: $(git -C "$REPO_ROOT" rev-parse --short "$BASE_REF")"
echo "head commit: $(git -C "$REPO_ROOT" rev-parse --short HEAD)"

before_target=$(run_suite "$WT/minimax/demo_project" tests/test_order_filters.py "$LOG/before-target.log")
before_regression=$(run_suite "$WT/minimax/demo_project" tests/test_service_regression.py "$LOG/before-regression.log")
after_target=$(run_suite "$MINIMAX_DIR/demo_project" tests/test_order_filters.py "$LOG/after-target.log")
after_regression=$(run_suite "$MINIMAX_DIR/demo_project" tests/test_service_regression.py "$LOG/after-regression.log")

mkdir -p "$MINIMAX_DIR/evidence/diff"
git -C "$REPO_ROOT" diff "$BASE_REF" HEAD -- minimax/demo_project > "$MINIMAX_DIR/evidence/diff/req-001.patch"

"$PY" - "$LOG" "$before_target" "$before_regression" "$after_target" "$after_regression" <<'PY'
import json
import sys
from pathlib import Path

log_dir = Path(sys.argv[1])
before_target, before_reg, after_target, after_reg = map(int, sys.argv[2:6])
checks = {
    "target tests fail before the change": before_target != 0,
    "regression tests pass before the change": before_reg == 0,
    "target tests pass after the change": after_target == 0,
    "regression tests pass after the change": after_reg == 0,
}
summary = {"exit_codes": {"before_target": before_target, "before_regression": before_reg,
                          "after_target": after_target, "after_regression": after_reg},
           "checks": checks, "result": "PASS" if all(checks.values()) else "FAIL"}
(log_dir / "delta-summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
raise SystemExit(0 if all(checks.values()) else 1)
PY
