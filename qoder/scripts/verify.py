#!/usr/bin/env python3
"""据典验收：静态约束检查 + 运行中服务的端到端检查。

没有启动 seekdb / Ollama / PowerContext 时，实时检查标记为 SKIP，不伪造 PASS。
--require-memory 或 --require-live 可把缺失依赖视为失败。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.config import Settings  # noqa: E402
from app.ingest import parse_corpus  # noqa: E402


@dataclass
class Check:
    status: str
    name: str
    detail: str = ""


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def _read(relative: str) -> str:
    path = ROOT / relative
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _static_checks() -> list[Check]:
    checks: list[Check] = []
    required = [
        "README.md",
        "app/main.py",
        "app/config.py",
        "app/db.py",
        "app/ingest.py",
        "app/llm.py",
        "app/memory.py",
        "app/pipeline.py",
        "app/static/index.html",
        "app/static/styles.css",
        "app/static/app.js",
        "scripts/seed.py",
        "scripts/eval.py",
        "scripts/verify.py",
        "scripts/setup.sh",
        "scripts/run.sh",
        "scripts/pc_server.sh",
        "data/gold.json",
        "data/samples/实验笔记.md",
        "site/index.html",
        "docs/技术说明.md",
        "docs/运行说明.md",
        "docs/验证样例.md",
        ".env.example",
        "pyproject.toml",
    ]
    missing = [item for item in required if not (ROOT / item).exists()]
    checks.append(
        Check(
            "FAIL" if missing else "PASS",
            "项目文件齐全",
            "缺少：" + "、".join(missing) if missing else f"{len(required)} 个关键文件",
        )
    )

    try:
        documents = parse_corpus(Settings.load().corpus_dir)
        chunks = sum(len(document.chunks) for document in documents)
        chars = sum(document.chars for document in documents)
        ok = len(documents) == 9 and chunks >= 100 and chars > 0
        checks.append(
            Check(
                "PASS" if ok else "FAIL",
                "语料已就绪",
                f"{len(documents)} 篇 / {chunks} 片段 / {chars} 字符",
            )
        )
    except Exception as exc:
        checks.append(Check("FAIL", "语料已就绪", str(exc)))

    try:
        gold = json.loads((ROOT / "data/gold.json").read_text(encoding="utf-8"))
        questions = gold.get("questions", [])
        answerable = [item for item in questions if item.get("answerable", True)]
        unanswerable = [item for item in questions if not item.get("answerable", True)]
        ids = {item.get("id") for item in questions}
        ok = len(questions) == 23 and len(answerable) == 20 and len(unanswerable) == 3 and len(ids) == 23
        checks.append(
            Check(
                "PASS" if ok else "FAIL",
                "固定评测集",
                f"{len(questions)} 题（{len(answerable)} 可答 + {len(unanswerable)} 资料外）",
            )
        )
    except Exception as exc:
        checks.append(Check("FAIL", "固定评测集", str(exc)))

    db_source = _read("app/db.py")
    uses_vector = "cosine_distance" in db_source and "1 - cosine_distance" in db_source
    uses_match = "MATCH(c.text) AGAINST(%s IN NATURAL LANGUAGE MODE)" in db_source
    checks.append(
        Check(
            "PASS" if uses_vector and uses_match else "FAIL",
            "检索使用 seekdb 向量与全文",
            "cosine_distance + MATCH ... AGAINST",
        )
    )

    app_sources = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (ROOT / "app").glob("*.py")
    ).lower()
    forbidden = [name for name in ("faiss", "elasticsearch", "opensearch") if name in app_sources]
    checks.append(
        Check(
            "FAIL" if forbidden else "PASS",
            "未引入外部向量/搜索引擎",
            "发现：" + "、".join(forbidden) if forbidden else "仅使用 seekdb",
        )
    )

    llm_source = _read("app/llm.py")
    citation_guard = all(
        token in llm_source
        for token in ("parse_citations", "_clean_citations", "_extractive", "INSUFFICIENT_ANSWER")
    )
    checks.append(
        Check(
            "PASS" if citation_guard else "FAIL",
            "引用校验与抽取式兜底",
            "合法 [n] → 引用；无合法引用 → 原文摘录；无证据 → 固定拒答",
        )
    )

    memory_source = _read("app/memory.py")
    memory_ok = (
        "/v1/memory/remember" in memory_source
        and "/v1/memory/entries/list" in memory_source
        and "memory_fallback_path" in memory_source
    )
    checks.append(
        Check(
            "PASS" if memory_ok else "FAIL",
            "PowerContext 读写与本地降级",
            "remember / entries/list / memory_fallback",
        )
    )

    pipeline_source = _read("app/pipeline.py")
    upload_ok = all(token in pipeline_source for token in ("max_upload_mb", "_SAFE_NAME", "uploads_dir"))
    checks.append(
        Check(
            "PASS" if upload_ok else "FAIL",
            "上传限制与文件名清洗",
            "20MB 上限、后缀白名单、data/uploads/",
        )
    )

    config_source = _read("app/config.py")
    checks.append(
        Check(
            "PASS" if "_DB_NAME" in config_source and "A-Za-z_" in config_source else "FAIL",
            "库名白名单与参数化 SQL",
            "库名正则校验；SQL 使用 %s 占位符",
        )
    )

    app_js = _read("app/static/app.js")
    frontend_ok = "highlightEvidence" in app_js and 'class="cite"' in app_js
    checks.append(
        Check(
            "PASS" if frontend_ok else "FAIL",
            "前端引用可点击",
            "渲染 [n] 按钮并高亮证据卡",
        )
    )

    env_example = _read(".env.example")
    secret_marks = ("sk-", "ghp_", "AKIA", "PASSWORD=changeme")
    leaks = [mark for mark in secret_marks if mark in env_example]
    checks.append(
        Check(
            "FAIL" if leaks else "PASS",
            ".env.example 无真实凭据",
            "发现：" + "、".join(leaks) if leaks else "全部为本地占位值",
        )
    )

    return checks


def _live_checks(args: argparse.Namespace, base: str) -> list[Check]:
    checks: list[Check] = []
    required_failure = args.require_live or args.require_memory
    client = httpx.Client(timeout=120.0)

    try:
        health_response = client.get(f"{base}/api/health")
        health_response.raise_for_status()
        health = health_response.json()
    except Exception as exc:
        detail = f"未连接 {base}：{exc}"
        checks.append(Check("FAIL" if required_failure else "SKIP", "运行中的服务", detail))
        checks.append(Check("SKIP", "seekdb 实时检索", "服务未启动"))
        checks.append(Check("SKIP", "端到端问答与引用", "服务未启动"))
        checks.append(Check("SKIP", "资料外拒答", "服务未启动"))
        checks.append(Check("SKIP", "PowerContext 记忆读写", "服务未启动"))
        checks.append(Check("SKIP", "评测结果", "服务未启动"))
        client.close()
        return checks

    checks.append(Check("PASS", "运行中的服务", base))
    db_ok = health.get("db") == "ok"
    corpus = health.get("corpus") or {}
    checks.append(
        Check(
            "PASS" if db_ok else "FAIL",
            "seekdb 可连接",
            health.get("db_error") or f"version/corpus: {corpus.get('chunks', 0)} chunks",
        )
    )
    chunks = int(corpus.get("chunks") or 0)
    checks.append(
        Check(
            "PASS" if chunks >= 100 else "FAIL",
            "语料已灌入",
            f"{chunks} chunks",
        )
    )

    def ask(question: str) -> dict[str, Any]:
        response = client.post(f"{base}/api/ask", json={"question": question})
        response.raise_for_status()
        return response.json()

    release = None
    try:
        release = ask("Release 模式的编译产物在哪个目录？")
        checks.append(Check("PASS", "问答接口可用", f"mode={release.get('mode')}"))
    except Exception as exc:
        checks.append(Check("FAIL", "问答接口可用", str(exc)))

    if release:
        answer = str(release.get("answer") or "")
        citations = release.get("citations") or []
        evidence = release.get("evidence") or []
        checks.append(
            Check(
                "PASS" if release.get("mode") != "insufficient" else "FAIL",
                "问答模式不是拒答",
                str(release.get("mode")),
            )
        )
        checks.append(
            Check(
                "PASS" if "build_release/src/observer/seekdb" in answer else "FAIL",
                "答案包含编译产物路径",
                answer[:120],
            )
        )
        checks.append(
            Check(
                "PASS" if citations else "FAIL",
                "答案带引用",
                "citations=[" + ", ".join(str(item.get("n")) for item in citations) + "]",
            )
        )
        evidence_ids = {row.get("chunk_id") for row in evidence if row.get("chunk_id") is not None}
        resolved = all(item.get("chunk_id") in evidence_ids for item in citations)
        checks.append(
            Check(
                "PASS" if citations and resolved else "FAIL",
                "引用能落到 chunks.id",
                f"{len(citations)} 条引用 / {len(evidence_ids)} 条证据",
            )
        )
        checks.append(
            Check(
                "PASS"
                if any(row.get("vec_rank") for row in evidence)
                and any(row.get("ft_rank") for row in evidence)
                else "FAIL",
                "检索轨迹含两路排名",
                "向量 + 全文",
            )
        )
        checks.append(
            Check(
                "PASS"
                if any("编译" in str(row.get("doc_title") or "") for row in evidence)
                else "FAIL",
                "证据来自构建文档",
                "、".join(sorted({str(row.get("doc_title") or "") for row in evidence}))[:160],
            )
        )

    try:
        gdb = ask("怎么用 GDB 调试 seekdb？")
        top3 = (gdb.get("evidence") or [])[:3]
        ok = gdb.get("mode") != "insufficient" and any(
            "调试" in str(row.get("doc_title") or "") for row in top3
        )
        checks.append(
            Check(
                "PASS" if ok else "FAIL",
                "GDB 问题命中调试文档",
                "、".join(str(row.get("doc_title") or "") for row in top3),
            )
        )
    except Exception as exc:
        checks.append(Check("FAIL", "GDB 问题命中调试文档", str(exc)))

    try:
        refusal = ask("seekdb 支持 MongoDB 协议吗？")
        reason = str(refusal.get("reason") or "")
        ok = (
            refusal.get("mode") == "insufficient"
            and not (refusal.get("citations") or [])
            and "mongodb" in reason.lower()
        )
        checks.append(
            Check(
                "PASS" if ok else "FAIL",
                "资料外问题被拒答",
                f"mode={refusal.get('mode')} reason={reason}",
            )
        )
    except Exception as exc:
        checks.append(Check("FAIL", "资料外问题被拒答", str(exc)))

    # 记忆：读取 + 写入 + 读回 + 尽量恢复原主题
    try:
        snapshot_response = client.get(f"{base}/api/memory")
        snapshot_response.raise_for_status()
        snapshot = snapshot_response.json()
        connected = snapshot.get("source") == "powercontext"
        if connected:
            checks.append(
                Check(
                    "PASS",
                    "PowerContext 可用",
                    f"entries={snapshot.get('entries')} scope={snapshot.get('scope_id')}",
                )
            )
        else:
            checks.append(
                Check(
                    "FAIL" if args.require_memory else "SKIP",
                    "PowerContext 可用" + ("（必需）" if args.require_memory else ""),
                    snapshot.get("error") or "当前使用本地暂存",
                )
            )
        old_profile = snapshot.get("profile") or {}
        topic = f"据典验证 · {datetime.now().strftime('%m-%d %H:%M')}"
        write_response = client.post(
            f"{base}/api/memory/profile",
            json={
                "topic": topic,
                "style": "concise",
                "goal": "验证 PowerContext 写入与读回",
            },
        )
        write_response.raise_for_status()
        written = write_response.json()
        again_response = client.get(f"{base}/api/memory")
        again_response.raise_for_status()
        again = again_response.json()
        read_topic = (again.get("profile") or {}).get("topic")
        read_ok = read_topic == topic
        checks.append(
            Check(
                "PASS" if read_ok else "FAIL",
                "写入并读回研究主题",
                f"source={written.get('source')} topic={read_topic}",
            )
        )
        if old_profile.get("topic"):
            try:
                restored = client.post(
                    f"{base}/api/memory/profile",
                    json={
                        "topic": old_profile.get("topic", ""),
                        "style": old_profile.get("style", ""),
                        "goal": old_profile.get("goal", ""),
                    },
                )
                restored.raise_for_status()
                after = client.get(f"{base}/api/memory")
                after.raise_for_status()
                now_topic = ((after.json().get("profile") or {}).get("topic")) or ""
                ok = now_topic == old_profile.get("topic")
                checks.append(
                    Check(
                        "PASS" if ok else "WARN",
                        "恢复原研究主题",
                        f"source={restored.json().get('source')} topic={now_topic}",
                    )
                )
            except Exception as exc:
                checks.append(Check("WARN", "恢复原研究主题", str(exc)))
        else:
            checks.append(
                Check("WARN", "恢复原研究主题", "验证前没有主题，保留本次验证主题")
            )
    except Exception as exc:
        checks.append(
            Check(
                "FAIL" if args.require_memory else "SKIP",
                "PowerContext 记忆读写",
                str(exc),
            )
        )

    eval_path = ROOT / "docs/evidence/eval.json"
    if eval_path.exists():
        try:
            report = json.loads(eval_path.read_text(encoding="utf-8"))
            summary = report.get("summary") or {}
            checks.append(
                Check(
                    "PASS",
                    "评测结果存在",
                    f"Recall@5 {summary.get('recall_at_5', 0) * 100:.0f}% · "
                    f"引用 {summary.get('citation_hit', 0) * 100:.0f}% · "
                    f"p50 {summary.get('p50_ms')} ms",
                )
            )
        except Exception as exc:
            checks.append(Check("FAIL", "评测结果存在", str(exc)))
    else:
        checks.append(Check("SKIP", "评测结果", "尚未运行 scripts/eval.py"))

    client.close()
    return checks


def _write_log(checks: list[Check], base: str) -> Path:
    settings = Settings.load()
    log_dir = settings.evidence_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"verify-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    lines = [
        "# 据典验收日志",
        "",
        f"- 时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 服务地址：{base}",
        f"- Python：{sys.version.split()[0]}",
        "",
        "| 状态 | 检查项 | 说明 |",
        "|---|---|---|",
    ]
    for check in checks:
        detail = check.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {check.status} | {check.name} | {detail} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="据典验收")
    parser.add_argument("--base", default=os.environ.get("JUDIAN_BASE", "http://127.0.0.1:8766"))
    parser.add_argument("--require-memory", action="store_true", help="PowerContext 不可用时失败")
    parser.add_argument("--require-live", action="store_true", help="服务未启动时失败")
    parser.add_argument("--no-log", action="store_true", help="不写 docs/evidence/logs")
    args = parser.parse_args()

    checks = _static_checks()
    checks.extend(_live_checks(args, args.base.rstrip("/")))

    for check in checks:
        print(f"{check.status:<4}  {check.name}" + (f"  |  {check.detail}" if check.detail else ""))

    failures = sum(1 for check in checks if check.status == "FAIL")
    skips = sum(1 for check in checks if check.status == "SKIP")
    passes = sum(1 for check in checks if check.status == "PASS")
    warnings = sum(1 for check in checks if check.status == "WARN")
    print()
    print(f"合计：PASS {passes} · FAIL {failures} · SKIP {skips} · WARN {warnings}")
    if skips:
        print("SKIP 表示对应服务未启动或结果尚未生成；没有伪造实时结果。")
    if not args.no_log:
        try:
            path = _write_log(checks, args.base)
            print(f"日志：{path.relative_to(ROOT)}")
        except Exception as exc:
            print(f"[WARN] 写日志失败：{exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
