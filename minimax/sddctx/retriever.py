"""Retrieve the pieces of engineering material a task needs, and render them.

Selection is data-driven: full-text search runs in seekdb, then requirement ids
are used to follow the traceability links to code symbols and tests. Nothing in
this module contains task-specific answers; re-importing an edited specification
changes the output.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from .db import SeekDB
from .importer import REQ_RE, req_ids

MAX_SPECS = 3


class StaleIndexError(RuntimeError):
    """Raised when a stored chunk no longer matches the file on disk."""


def _iso_now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _spec_entries(db: SeekDB, project: str, task: str) -> tuple[list[dict[str, Any]], list[str]]:
    mentioned = req_ids(task)
    rows = db.search_spec_chunks(project, task, limit=8)
    if not rows:
        rows = db.spec_chunks(project, limit=20)

    for row in rows:
        own = REQ_RE.findall(row["symbol"])
        row["req_ids"] = sorted(set(own)) if own else req_ids(row["content"])
        bonus = 5.0 * len(set(row["req_ids"]) & set(mentioned))
        if row.get("change_kind") == "new":
            bonus += 2.0
        row["score"] = float(row.get("score") or 0.0) + bonus

    rows.sort(key=lambda item: item["score"], reverse=True)
    selected = [row for row in rows if row["score"] > 0][:MAX_SPECS] or rows[:1]

    # A requirement named explicitly in the task must be represented even when
    # its full-text score is low.
    if mentioned:
        selected_ids = {row["id"] for row in selected}
        for row in db.chunks_for_reqs(project, "spec", mentioned):
            if row["id"] not in selected_ids:
                row["score"] = 1.0
                row["req_ids"] = req_ids(row["content"])
                selected.append(row)
                selected_ids.add(row["id"])

    return selected, mentioned


def _load_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def verify_source(root: Path, item: dict[str, Any]) -> None:
    """Fail loudly when a stored chunk does not match the file it claims to cite."""
    path = root / item["rel_path"]
    if not path.is_file():
        raise StaleIndexError(f"source file missing: {item['rel_path']}")
    lines = _load_lines(path)
    start, end = item["line_start"], item["line_end"]
    if start < 1 or end > len(lines):
        raise StaleIndexError(
            f"line range {start}-{end} outside {item['rel_path']} ({len(lines)} lines)"
        )
    actual = "\n".join(lines[start - 1 : end])
    if item.get("content") and actual != item["content"]:
        raise StaleIndexError(
            f"stale index for {item['rel_path']}:{start}-{end}; run `sddctx import` again"
        )


def build_context(
    db: SeekDB,
    project: str,
    root: Path,
    task: str,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    specs, mentioned = _spec_entries(db, project, task)
    for spec in specs:
        verify_source(root, spec)

    selected_reqs = sorted({req for spec in specs for req in spec["req_ids"]})
    new_reqs = db.reqs_for_change_kind(project, "new")
    code_rows = db.chunks_for_reqs(project, "code", sorted(set(selected_reqs) | set(new_reqs)))
    specific_new = [req for req in new_reqs if "." in req] or new_reqs
    specific = [
        row for row in code_rows
        if set((row["reqs"] or "").split(",")) & set(specific_new)
    ]
    if specific:
        code_rows = specific
    if new_reqs:
        test_rows = db.chunks_for_reqs(project, "test", new_reqs)
    else:
        test_rows = db.chunks_for_reqs(project, "test", selected_reqs)
    for row in code_rows + test_rows:
        verify_source(root, row)

    target_ids = [row["id"] for row in test_rows]
    regression_rows = db.other_test_chunks(project, target_ids)

    code_by_file: dict[str, dict[str, Any]] = {}
    for row in code_rows:
        entry = code_by_file.setdefault(
            row["rel_path"], {"path": row["rel_path"], "symbols": [], "req_ids": set()}
        )
        entry["symbols"].append(
            {
                "name": row["symbol"],
                "lines": [row["line_start"], row["line_end"]],
                "req_ids": (row["reqs"] or "").split(","),
            }
        )
        entry["req_ids"].update((row["reqs"] or "").split(","))

    for entry in code_by_file.values():
        entry["symbols"].sort(key=lambda symbol: symbol["lines"][0])
        entry["req_ids"] = sorted(req for req in entry["req_ids"] if req)

    return {
        "task": task,
        "project": project,
        "root": str(root),
        "generated_at": _iso_now(),
        "selection": {
            "method": "seekdb full-text search over spec chunks, then REQ traceability join",
            "mentioned_requirements": mentioned,
            "selected_requirements": selected_reqs,
            "new_requirements": new_reqs,
            "spec_scores": [
                {"path": row["rel_path"], "symbol": row["symbol"], "score": round(row["score"], 4)}
                for row in specs
            ],
        },
        "specs": [
            {
                "req_ids": row["req_ids"],
                "path": row["rel_path"],
                "symbol": row["symbol"],
                "title": row["title"],
                "lines": [row["line_start"], row["line_end"]],
                "version": row["version"],
                "change_kind": row.get("change_kind", "existing"),
                "score": round(row["score"], 4),
                "excerpt": row["content"],
            }
            for row in specs
        ],
        "code": sorted(code_by_file.values(), key=lambda item: item["path"]),
        "files_to_modify": sorted(code_by_file),
        "tests": {
            "target": [
                {
                    "path": row["rel_path"],
                    "symbol": row["symbol"],
                    "title": row["title"],
                    "lines": [row["line_start"], row["line_end"]],
                    "req_ids": (row["reqs"] or "").split(","),
                }
                for row in test_rows
            ],
            "regression": [
                {
                    "path": row["rel_path"],
                    "symbol": row["symbol"],
                    "title": row["title"],
                    "lines": [row["line_start"], row["line_end"]],
                    "req_ids": (row["reqs"] or "").split(","),
                }
                for row in regression_rows
            ],
        },
        "state": state,
        "sources_verified": True,
    }


def render_markdown(context: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# 开发上下文 · {context['project']}")
    lines.append("")
    lines.append(f"- 任务：{context['task']}")
    lines.append(f"- 生成时间：{context['generated_at']}")
    lines.append(f"- 选择方式：{context['selection']['method']}")
    if context["selection"]["selected_requirements"]:
        lines.append(
            "- 选中需求：" + ", ".join(context["selection"]["selected_requirements"])
        )
    lines.append("")

    lines.append(f"## 1. 适用规格（{len(context['specs'])}）")
    for spec in context["specs"]:
        reqs = ", ".join(spec["req_ids"]) or "（无编号）"
        lines.append("")
        lines.append(f"### {spec['symbol']}")
        lines.append(
            f"- 来源：`{spec['path']}:{spec['lines'][0]}-{spec['lines'][1]}`"
            f"（版本 {spec['version']}，匹配分 {spec['score']}，需求 {reqs}）"
        )
        for chunk_line in spec["excerpt"].splitlines():
            lines.append(f"  > {chunk_line}" if chunk_line else "  >")
    lines.append("")

    lines.append("## 2. 需要修改的代码")
    if not context["code"]:
        lines.append("（未找到与选中需求关联的代码）")
    for item in context["code"]:
        lines.append(f"- `{item['path']}`  需求：{', '.join(item['req_ids'])}")
        for symbol in item["symbols"]:
            lines.append(
                f"  - `{symbol['name']}` 行 {symbol['lines'][0]}-{symbol['lines'][1]}"
                f"（{', '.join(symbol['req_ids'])}）"
            )
    lines.append("")

    lines.append("## 3. 验证测试")
    lines.append("")
    lines.append(f"### 3.1 目标功能测试（{len(context['tests']['target'])}）")
    for test in context["tests"]["target"]:
        lines.append(
            f"- `{test['path']}::{test['symbol']}` 行 {test['lines'][0]}-{test['lines'][1]}"
            f"（{', '.join(test['req_ids'])}）"
        )
    lines.append("")
    lines.append(f"### 3.2 回归测试（{len(context['tests']['regression'])}）")
    for test in context["tests"]["regression"]:
        reqs = ", ".join(test["req_ids"]) or "无标注"
        lines.append(
            f"- `{test['path']}::{test['symbol']}` 行 {test['lines'][0]}-{test['lines'][1]}"
            f"（{reqs}）"
        )
    lines.append("")

    lines.append("## 4. 任务状态（PowerContext）")
    state = context.get("state")
    if not state:
        lines.append("（尚未保存任务状态）")
    else:
        lines.append(f"- 任务 id：{state.get('task_id')}")
        lines.append(f"- 阶段：{state.get('stage')}")
        lines.append(f"- 更新时间：{state.get('saved_at')}")
        lines.append("- 已完成：")
        for item in state.get("done", []):
            lines.append(f"  - [x] {item}")
        lines.append("- 待办：")
        for item in state.get("todo", []) or ["（无）"]:
            lines.append(f"  - [ ] {item}")
        lines.append("- 验证结果：")
        for record in state.get("verifications", []) or [{"command": "（无）"}]:
            if "exit_code" in record:
                lines.append(
                    f"  - `{record['command']}` → exit {record['exit_code']}"
                    f"（{record.get('at', '')}）"
                )
            else:
                lines.append(f"  - {record['command']}")
    lines.append("")
    return "\n".join(lines)
