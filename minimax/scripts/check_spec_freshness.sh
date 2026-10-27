#!/usr/bin/env bash
# Re-import proof: edit a specification, re-import it, and show that the context
# output follows the new content instead of returning a fixed answer.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MINIMAX_DIR="$(cd "$HERE/.." && pwd)"
PY="$MINIMAX_DIR/.venv/bin/python"
cd "$MINIMAX_DIR"

WORK=".runtime/spec-drift"
rm -rf "$WORK"
mkdir -p "$WORK"
cp -r samples/spec-drift/specs "$WORK/specs"

LOG_DIR="evidence/logs"
mkdir -p "$LOG_DIR"

TASK="调整审计日志导出文件的保留期限并确认编码要求"

echo "== before edit =="
"$PY" -m sddctx import --project "$WORK" --name drift --reset > /dev/null
"$PY" -m sddctx context --project "$WORK" --name drift --task "$TASK" --no-state --json \
  > "$LOG_DIR/spec-drift-before.json"

echo "== edit spec: 30 days -> 90 days, version 1 -> 2, add a privacy clause =="
"$PY" - "$WORK/specs/REQ-010-audit-export.md" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.replace("- 版本：1", "- 版本：2")
text = text.replace("保留 30 天", "保留 90 天")
text = text.replace(
    "导出文件使用 UTF-8 编码，字段以逗号分隔；文件名格式为 `audit-YYYYMMDD.csv`。",
    "导出文件使用 UTF-8 编码，字段以逗号分隔；文件名格式为 `audit-YYYYMMDD.csv`。"
    "导出内容不得包含用户手机号。",
)
path.write_text(text, encoding="utf-8")
print("spec updated")
PY

echo "== after edit (re-import) =="
"$PY" -m sddctx import --project "$WORK" --name drift > /dev/null
"$PY" -m sddctx context --project "$WORK" --name drift --task "$TASK" --no-state --json \
  > "$LOG_DIR/spec-drift-after.json"

"$PY" - <<'PY'
import json
from pathlib import Path

before = json.loads(Path("evidence/logs/spec-drift-before.json").read_text())
after = json.loads(Path("evidence/logs/spec-drift-after.json").read_text())

def spec_text(ctx, req):
    for spec in ctx["specs"]:
        if req in spec["req_ids"]:
            return spec
    return None

b = spec_text(before, "REQ-010.1")
a = spec_text(after, "REQ-010.1")
assert b and a, "REQ-010.1 missing from context"
assert "30 天" in b["excerpt"], b["excerpt"]
assert "90 天" in a["excerpt"], a["excerpt"]
assert b["version"] == 1 and a["version"] == 2, (b["version"], a["version"])
assert "手机号" in spec_text(after, "REQ-010.2")["excerpt"]

def rule_line(spec):
    for line in spec["excerpt"].splitlines():
        if line.startswith("#") or not line.strip():
            continue
        if "天" in line or "UTF-8" in line:
            return line.strip()
    return spec["excerpt"].splitlines()[0]

print(json.dumps({
    "before": {"version": b["version"], "rule": rule_line(b)},
    "after": {"version": a["version"], "rule": rule_line(a)},
    "result": "PASS: context follows the re-imported specification",
}, ensure_ascii=False, indent=2))
PY
