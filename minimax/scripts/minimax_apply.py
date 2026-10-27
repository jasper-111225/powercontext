#!/usr/bin/env python3
"""Let MiniMax perform the code change described by a saved context.

Usage:
    MINIMAX_API_KEY=... .venv/bin/python scripts/minimax_apply.py \\
        --context evidence/logs/02-context.json --model MiniMax-M3

Every call writes an auditable record under ``evidence/minimax/``: the prompt
sent to MiniMax (context, specs, code, tests), the raw response, token usage,
and the files that were written. The API key is never written to disk.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path

MINIMAX_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MINIMAX_DIR))

from sddctx.minimax import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    MiniMaxClient,
    MiniMaxError,
    apply_files,
    build_messages,
    parse_edit_response,
)


def _stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _api_key(args: argparse.Namespace) -> str:
    key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not key and args.key_file:
        path = Path(args.key_file)
        if path.exists():
            key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit(
            "MINIMAX_API_KEY is empty. Export your MiniMax Token Plan key or pass --key-file."
        )
    return key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="context JSON written by sddctx")
    parser.add_argument("--project", default=str(MINIMAX_DIR / "demo_project"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--record-dir", default=str(MINIMAX_DIR / "evidence" / "minimax"))
    parser.add_argument("--key-file", default=str(MINIMAX_DIR / ".runtime" / "minimax.key"))
    parser.add_argument("--extra-instructions", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    context = json.loads(Path(args.context).read_text(encoding="utf-8"))
    project = Path(args.project).resolve()
    if context.get("root") and Path(context["root"]).resolve() != project:
        raise SystemExit(
            f"context root {context['root']} does not match --project {project}; rebuild the context"
        )

    messages = build_messages(context, project, args.extra_instructions)
    prompt_chars = sum(len(message["content"]) for message in messages)
    record_dir = Path(args.record_dir)
    record_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()

    request_record = {
        "stamp": stamp,
        "model": args.model,
        "base_url": args.base_url,
        "context_sha256": _sha(json.dumps(context, ensure_ascii=False, sort_keys=True)),
        "context_file": str(Path(args.context).resolve()),
        "task": context["task"],
        "specs": [spec["path"] for spec in context["specs"]],
        "files_to_modify": context["files_to_modify"],
        "prompt_chars": prompt_chars,
        "messages": messages,
        "sent_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    (record_dir / f"{stamp}-request.json").write_text(
        json.dumps(request_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if args.dry_run:
        print(json.dumps({"dry_run": True, "prompt_chars": prompt_chars,
                          "request_record": str(record_dir / f"{stamp}-request.json")},
                         ensure_ascii=False, indent=2))
        return 0

    client = MiniMaxClient(_api_key(args), args.base_url, args.model)
    started = time.time()
    try:
        result = client.chat(messages, temperature=0.2, max_tokens=8000)
    except MiniMaxError as exc:
        failure = {"stamp": stamp, "error": str(exc),
                   "failed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        (record_dir / f"{stamp}-error.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"MiniMax call failed: {exc}", file=sys.stderr)
        return 2
    elapsed = round(time.time() - started, 2)

    response_record = {
        "stamp": stamp,
        "model": result.get("model"),
        "usage": result.get("usage"),
        "elapsed_seconds": elapsed,
        "content": result["content"],
        "raw_response": result.get("raw"),
        "received_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    (record_dir / f"{stamp}-response.json").write_text(
        json.dumps(response_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    edit = parse_edit_response(result["content"])
    before = {
        item["path"]: _sha((project / item["path"]).read_text(encoding="utf-8"))
        for item in edit["files"]
        if (project / item["path"]).exists()
    }
    written = apply_files(project, edit["files"])
    after = {
        item["path"]: _sha((project / item["path"]).read_text(encoding="utf-8"))
        for item in edit["files"]
    }

    record = {
        "stamp": stamp,
        "model": result.get("model"),
        "summary": edit.get("summary", ""),
        "files": [
            {"path": item["path"], "before_sha256": before.get(item["path"]),
             "after_sha256": after.get(item["path"]), "bytes": item["bytes"],
             "changed": item["changed"]}
            for item in written
        ],
        "specs": [{"path": spec["path"], "lines": spec["lines"], "req_ids": spec["req_ids"]}
                  for spec in context["specs"]],
        "tests_target": [test["symbol"] for test in context["tests"]["target"]],
        "tests_regression": [test["symbol"] for test in context["tests"]["regression"]],
        "usage": result.get("usage"),
        "elapsed_seconds": elapsed,
        "prompt_chars": prompt_chars,
        "applied_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    (record_dir / f"{stamp}-applied.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_markdown(record, request_record, response_record, record_dir / f"{stamp}-record.md")

    print(json.dumps({"record": str(record_dir / f'{stamp}-record.md'),
                      "summary": record["summary"], "files": record["files"],
                      "usage": record["usage"], "elapsed_seconds": elapsed},
                     ensure_ascii=False, indent=2))
    return 0


def _write_markdown(record: dict, request: dict, response: dict, path: Path) -> None:
    lines = [
        f"# MiniMax 开发记录 · {record['stamp']}",
        "",
        f"- 模型：`{record['model']}`",
        f"- 任务：{request['task']}",
        f"- 上下文摘要（sha256）：`{request['context_sha256']}`",
        f"- 提示词规模：{request['prompt_chars']} 字符",
        f"- 调用耗时：{record['elapsed_seconds']} 秒",
        f"- Token 用量：`{json.dumps(record['usage'], ensure_ascii=False)}`",
        "",
        "## 提供给 MiniMax 的上下文",
        "",
        f"- 规格：{', '.join(request['specs'])}",
        f"- 待改文件：{', '.join(request['files_to_modify'])}",
        f"- 目标测试：{', '.join(record['tests_target'])}",
        f"- 回归测试：{', '.join(record['tests_regression'])}",
        "",
        "## MiniMax 的改动说明",
        "",
        f"> {record['summary'] or '(无)'}",
        "",
        "## 写回文件",
        "",
    ]
    for item in record["files"]:
        lines.append(
            f"- `{item['path']}`：{item['bytes']} 字节，"
            f"sha256 {str(item['before_sha256'])[:12]} → {str(item['after_sha256'])[:12]}"
        )
    lines += [
        "",
        "## 原始记录",
        "",
        f"- 请求：`{request['stamp']}-request.json`",
        f"- 响应：`{request['stamp']}-response.json`",
        f"- 应用结果：`{record['stamp']}-applied.json`",
        "",
        "## 模型原始输出（截断至 4000 字符）",
        "",
        "```json",
        response["content"][:4000],
        "```",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
