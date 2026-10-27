#!/usr/bin/env bash
# End-to-end demo: import -> context -> tests -> PowerContext state -> restart -> reuse.
# Records every command and exit code under evidence/logs/.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MINIMAX_DIR="$(cd "$HERE/.." && pwd)"
cd "$MINIMAX_DIR"

PY="$MINIMAX_DIR/.venv/bin/python"
LOG="$MINIMAX_DIR/evidence/logs"
PROJECT="demo_project"
NAME="minimart"
TASK="为订单查询增加状态筛选与最低金额筛选：支持 status 与 min_total 参数，过滤后分页，保持创建时间倒序，并校验非法参数。"
mkdir -p "$LOG"
FRESH="${FRESH:-1}"

step() { printf '\n=== %s ===\n' "$1"; }

run_suite() { # <project_dir> <test_path> <log_file>
  ( cd "$1" && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PY" -m pytest -q "$2" ) > "$3" 2>&1
  echo $?
}

if [[ "$FRESH" == "1" ]]; then
  step "0.0 重置环境（seekdb 库 + PowerContext 数据）"
  ./scripts/reset_environment.sh | tee "$LOG/00-reset.log"
fi

step "0. 检查 seekdb 与 PowerContext"
{
  echo "git commit: $(git -C "$MINIMAX_DIR" rev-parse HEAD)"
  echo "python: $("$PY" -V)"
  echo "powercontext: $(powercontext --version 2>/dev/null || echo unknown)"
} | tee "$LOG/00-versions.log"
"$PY" -m sddctx doctor | tee "$LOG/00-doctor.log"
doctor_exit=${PIPESTATUS[0]}

step "1. 启动 PowerContext Server"
./scripts/pc_server.sh start | tee "$LOG/01-powercontext-start.log"
"$PY" -m sddctx state show --project "$PROJECT" --name "$NAME" | tee "$LOG/01-state-before.log"

step "2. 导入规格/代码/测试到 seekdb"
"$PY" -m sddctx import --project "$PROJECT" --name "$NAME" --reset | tee "$LOG/02-import.log"
import_exit=${PIPESTATUS[0]}

step "3. 为任务生成上下文"
"$PY" -m sddctx context --project "$PROJECT" --name "$NAME" --task "$TASK" \
  --json --out "$LOG/03-context.json" > "$LOG/03-context.json.out"
context_exit=$?
"$PY" -m sddctx context --project "$PROJECT" --name "$NAME" --task "$TASK" \
  > "$LOG/03-context.md"

step "4. 运行目标测试与回归测试"
target_exit=$(run_suite "$PROJECT" tests/test_order_filters.py "$LOG/04-target-tests.log")
regression_exit=$(run_suite "$PROJECT" tests/test_service_regression.py "$LOG/04-regression-tests.log")

step "5. 校验上下文来源真实存在"
"$PY" -m sddctx verify --context "$LOG/03-context.json" | tee "$LOG/05-verify-sources.log"
verify_exit=${PIPESTATUS[0]}

step "6. 保存任务状态到 PowerContext"
"$PY" -m sddctx state set --project "$PROJECT" --name "$NAME" \
  --stage verified --context "$LOG/03-context.json" \
  --done "导入规格/代码/测试到 seekdb" \
  --done "生成任务上下文并校验来源" \
  --done "用 MiniMax 完成 REQ-001 代码修改" \
  --done "目标测试与回归测试通过" \
  --verification "pytest tests/test_order_filters.py|$target_exit|evidence/logs/04-target-tests.log" \
  --verification "pytest tests/test_service_regression.py|$regression_exit|evidence/logs/04-regression-tests.log" \
  | tee "$LOG/06-state-save.log"
state_save_exit=${PIPESTATUS[0]}

step "7. 重启 PowerContext Server 后读取状态"
./scripts/pc_server.sh restart | tee "$LOG/07-powercontext-restart.log"
"$PY" -m sddctx state show --project "$PROJECT" --name "$NAME" | tee "$LOG/07-state-after-restart.log"
state_read_exit=${PIPESTATUS[0]}

step "8. 重启后重新生成上下文（应包含已保存状态）"
"$PY" -m sddctx context --project "$PROJECT" --name "$NAME" --task "$TASK" \
  --json --out "$LOG/08-context-after-restart.json" > "$LOG/08-context-after-restart.json.out"
"$PY" -m sddctx context --project "$PROJECT" --name "$NAME" --task "$TASK" \
  > "$LOG/08-context-after-restart.md"
reuse_exit=$?

step "9. 规格更新后重新导入（输出跟随新内容）"
./scripts/check_spec_freshness.sh > "$LOG/09-spec-freshness.log" 2>&1
freshness_exit=$?
tail -12 "$LOG/09-spec-freshness.log"

"$PY" - "$LOG" "$target_exit" "$regression_exit" "$freshness_exit" "$state_read_exit" <<'PY'
import json
import sys
from pathlib import Path

log_dir = Path(sys.argv[1])
target, regression, freshness, state_read = map(int, sys.argv[2:6])
state = json.loads((log_dir / "07-state-after-restart.log").read_text())
context = json.loads((log_dir / "08-context-after-restart.json").read_text())

before_state = json.loads((log_dir / "01-state-before.log").read_text())
checks = {
    "starts from empty task state": before_state.get("count") == 0,
    "target tests pass": target == 0,
    "regression tests pass": regression == 0,
    "context carries saved state": bool(context.get("state")),
    "state stage is verified": state.get("latest", {}).get("stage") == "verified",
    "verification results restored": len(context.get("state", {}).get("verifications", [])) == 2,
    "spec freshness check passes": freshness == 0,
    "state read after restart": state_read == 0,
}
summary = {"checks": checks, "exit_codes": {"target": target, "regression": regression,
                                            "freshness": freshness, "state_read": state_read},
           "result": "PASS" if all(checks.values()) else "FAIL"}
(log_dir / "10-demo-summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
raise SystemExit(0 if all(checks.values()) else 1)
PY
demo_exit=$?

echo
if [[ $demo_exit -eq 0 ]]; then
  echo "DEMO PASS — evidence in evidence/logs/"
else
  echo "DEMO FAIL — inspect evidence/logs/"
fi
exit $demo_exit
