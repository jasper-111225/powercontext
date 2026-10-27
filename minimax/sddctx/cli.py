"""Command line entry point for the SDD context module.

Commands
--------
``import``   chunk specs/code/tests into seekdb (re-runnable after edits)
``context``  select specs + code + tests for a task, merge PowerContext state
``state``    save or show task progress through PowerContext
``verify``   re-check that every source in a saved context still exists
``stats``    show what seekdb currently stores
``doctor``   check seekdb and PowerContext connectivity
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import load_settings
from .db import SeekDB
from .importer import import_project
from .powercontext import PowerContext, PowerContextError
from .retriever import StaleIndexError, build_context, render_markdown, verify_source


def _now() -> str:
    import datetime as dt

    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _project_name(args: argparse.Namespace) -> str:
    return args.name or Path(args.project).resolve().name


def _context_digest(context: dict[str, Any]) -> str:
    payload = json.dumps(context, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def cmd_import(args: argparse.Namespace) -> int:
    settings = load_settings()
    project = _project_name(args)
    root = Path(args.project).resolve()
    if not (root / "specs").is_dir():
        raise SystemExit(f"{root} has no specs/ directory")
    with SeekDB(settings) as db:
        notes = db.init_schema()
        if args.reset:
            db.purge_project(project)
        summary = import_project(db, root, project)
        summary["fulltext"] = db.fulltext_available()
        summary["schema_notes"] = notes
        summary["stats"] = db.stats(project)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _load_state(project: str, task_id: str | None) -> dict[str, Any] | None:
    try:
        client = PowerContext()
        if not client.health():
            print("warning: PowerContext Server is not ready; continuing without saved state",
                  file=sys.stderr)
            return None
        return client.latest_state(project, task_id)
    except PowerContextError as exc:
        print(f"warning: {exc}", file=sys.stderr)
        return None


def cmd_context(args: argparse.Namespace) -> int:
    project = _project_name(args)
    root = Path(args.project).resolve()
    state = None if args.no_state else _load_state(project, args.task_id)
    with SeekDB() as db:
        db.init_schema()
        context = build_context(db, project, root, args.task, state)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
        print(f"context written to {args.out}", file=sys.stderr)
    if args.json:
        print(json.dumps(context, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(context))
    return 0


def cmd_state_set(args: argparse.Namespace) -> int:
    project = _project_name(args)
    state: dict[str, Any] = {
        "task_id": args.task_id,
        "project": project,
        "stage": args.stage,
        "task": args.task or "",
        "done": list(args.done or []),
        "todo": list(args.todo or []),
        "verifications": [],
        "saved_at": _now(),
    }
    for record in args.verification or []:
        parts = record.split("|")
        if len(parts) != 3:
            raise SystemExit("--verification must be 'command|exit_code|log_path'")
        state["verifications"].append(
            {"command": parts[0], "exit_code": int(parts[1]), "log": parts[2], "at": _now()}
        )
    if args.context:
        context = json.loads(Path(args.context).read_text(encoding="utf-8"))
        state["task"] = state["task"] or context.get("task", "")
        state["requirements"] = context["selection"]["selected_requirements"]
        state["files_to_modify"] = context["files_to_modify"]
        state["context_digest"] = _context_digest(context)
        if not state["task_id"]:
            state["task_id"] = "sdd-" + state["context_digest"][:8]
    if not state["task_id"]:
        raise SystemExit("--task-id is required when --context is not given")

    client = PowerContext()
    saved = client.save_state(project, state)
    print(json.dumps(saved, ensure_ascii=False, indent=2))
    return 0


def cmd_state_show(args: argparse.Namespace) -> int:
    project = _project_name(args)
    client = PowerContext()
    states = client.load_states(project)
    payload = {"project": project, "count": len(states), "latest": states[-1] if states else None}
    if args.all:
        payload["states"] = states
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    context = json.loads(Path(args.context).read_text(encoding="utf-8"))
    root = Path(args.project or context["root"]).resolve()
    checked = 0
    for spec in context["specs"]:
        verify_source(root, {"rel_path": spec["path"], "line_start": spec["lines"][0],
                             "line_end": spec["lines"][1], "content": spec["excerpt"]})
        checked += 1
    for item in context["code"]:
        for symbol in item["symbols"]:
            path = root / item["path"]
            lines = path.read_text(encoding="utf-8").splitlines()
            start, end = symbol["lines"]
            if not (1 <= start <= end <= len(lines)):
                raise SystemExit(f"bad range {item['path']}:{start}-{end}")
            checked += 1
    for group in context["tests"].values():
        for test in group:
            path = root / test["path"]
            lines = path.read_text(encoding="utf-8").splitlines()
            start, end = test["lines"]
            if not (1 <= start <= end <= len(lines)):
                raise SystemExit(f"bad range {test['path']}:{start}-{end}")
            checked += 1
    print(json.dumps({"verified": True, "sources": checked, "root": str(root)},
                     ensure_ascii=False))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    with SeekDB() as db:
        db.init_schema()
        rows = db.stats(args.project)
        print(json.dumps({"rows": rows, "fulltext": db.fulltext_available()},
                         ensure_ascii=False, indent=2))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = load_settings()
    report: dict[str, Any] = {"seekdb": {}, "powercontext": {}}
    try:
        with SeekDB(settings) as db:
            row = db.query("SELECT version() AS version, current_user() AS user")[0]
            report["seekdb"] = {"ok": True, "version": row["version"], "user": row["user"]}
            db.init_schema()
            report["seekdb"]["fulltext"] = db.fulltext_available()
            report["seekdb"]["stats"] = db.stats()
    except Exception as exc:  # pragma: no cover - diagnostic path
        report["seekdb"] = {"ok": False, "error": str(exc)}
    client = PowerContext(settings)
    try:
        report["powercontext"] = {
            "ok": client.health(),
            "url": settings.powercontext_url,
        }
    except PowerContextError as exc:
        report["powercontext"] = {"ok": False, "error": str(exc)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["seekdb"].get("ok") and report["powercontext"].get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sddctx", description=__doc__)
    parser.add_argument("--version", action="version", version=f"sddctx {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="chunk project materials into seekdb")
    p_import.add_argument("--project", required=True)
    p_import.add_argument("--name")
    p_import.add_argument("--reset", action="store_true")
    p_import.set_defaults(func=cmd_import)

    p_context = sub.add_parser("context", help="build the development context for a task")
    p_context.add_argument("--project", required=True)
    p_context.add_argument("--name")
    p_context.add_argument("--task", required=True)
    p_context.add_argument("--task-id")
    p_context.add_argument("--json", action="store_true")
    p_context.add_argument("--no-state", action="store_true")
    p_context.add_argument("--out")
    p_context.set_defaults(func=cmd_context)

    p_state = sub.add_parser("state", help="save or show task state through PowerContext")
    state_sub = p_state.add_subparsers(dest="state_command", required=True)

    p_set = state_sub.add_parser("set")
    p_set.add_argument("--project", required=True)
    p_set.add_argument("--name")
    p_set.add_argument("--task-id")
    p_set.add_argument("--task")
    p_set.add_argument("--stage", required=True)
    p_set.add_argument("--done", action="append")
    p_set.add_argument("--todo", action="append")
    p_set.add_argument("--verification", action="append")
    p_set.add_argument("--context")
    p_set.set_defaults(func=cmd_state_set)

    p_show = state_sub.add_parser("show")
    p_show.add_argument("--project", required=True)
    p_show.add_argument("--name")
    p_show.add_argument("--all", action="store_true")
    p_show.set_defaults(func=cmd_state_show)

    p_verify = sub.add_parser("verify", help="check sources of a saved context")
    p_verify.add_argument("--context", required=True)
    p_verify.add_argument("--project")
    p_verify.set_defaults(func=cmd_verify)

    p_stats = sub.add_parser("stats", help="show seekdb inventory")
    p_stats.add_argument("--project")
    p_stats.set_defaults(func=cmd_stats)

    p_doctor = sub.add_parser("doctor", help="check seekdb and PowerContext")
    p_doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (StaleIndexError, PowerContextError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
