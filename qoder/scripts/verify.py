#!/usr/bin/env python
"""对运行中的「据典」做一组端到端验收，输出 PASS/FAIL 与可归档日志。

  python scripts/verify.py
  python scripts/verify.py --base http://127.0.0.1:8766 --require-memory
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "docs" / "evidence" / "logs"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []
        self.lines: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, ok, detail))
        line = f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  |  {detail}" if detail else "")
        print(line, flush=True)
        self.lines.append(line)

    def note(self, line: str) -> None:
        print("      " + line, flush=True)
        self.lines.append("      " + line)


def ask(client: httpx.Client, base: str, question: str, style: str = "concise") -> dict:
    response = client.post(f"{base}/api/ask", json={"question": question, "style": style}, timeout=180)
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    parser.add_argument("--require-memory", action="store_true")
    args = parser.parse_args()
    report = Report()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    with httpx.Client() as client:
        # 1. 健康
        health = client.get(f"{args.base}/api/health", timeout=20).json()
        report.check("seekdb 可连接", health.get("db") == "ok", str(health.get("db"))[:80])
        corpus = health.get("corpus") or {}
        report.check("语料已灌入", int(corpus.get("chunks", 0)) >= 20, f"{corpus.get('chunks')} chunks")
        memory_ok = health.get("memory") == "ok"
        report.check(
            "PowerContext 可用" + ("（必需）" if args.require_memory else "（可选）"),
            memory_ok or not args.require_memory,
            str(health.get("memory"))[:80],
        )

        # 2. 编译产物：答案要含路径，且引用非空
        r1 = ask(client, args.base, "Release 模式的编译产物在哪个目录？")
        text1 = r1["answer"]
        report.check("问答模式不是拒答", r1["mode"] != "insufficient", r1["mode"])
        report.check("答案包含编译产物路径", "build_release/src/observer/seekdb" in text1.replace(" ", ""))
        report.check("答案带引用", bool(r1["citations"]), f"citations={r1['citations']}")
        report.check(
            "证据来自构建文档",
            any("build-and-run" in (item.get("doc_title", "") + item.get("source", "")) for item in r1["evidence"]),
            str([item.get("doc_title") for item in r1["evidence"][:3]]),
        )
        report.check("检索轨迹含两路排名", bool(r1["trace"]["vector"]) and bool(r1["trace"]["fulltext"]))

        # 3. 调试问题：证据应来自 debug
        r2 = ask(client, args.base, "怎么用 GDB 调试 seekdb？")
        report.check(
            "GDB 问题的 Top-3 命中调试文档",
            any("调试" in item.get("doc_title", "") for item in r2["evidence"][:3]),
            str([item.get("doc_title") for item in r2["evidence"][:3]]),
        )

        # 4. 资料外问题：拒答
        r3 = ask(client, args.base, "seekdb 支持 MongoDB 协议吗？")
        report.check("资料外问题被拒答", r3["mode"] == "insufficient", f"mode={r3['mode']}")

        # 5. 记忆
        if memory_ok:
            before = client.get(f"{args.base}/api/memory", timeout=20).json()
            previous_profile = before.get("profile") or {}
            saved = client.post(
                f"{args.base}/api/memory/profile",
                json={
                    "topic": "seekdb 内核开发验收",
                    "style": "concise",
                    "goal": "确认记忆在重启后仍在",
                },
                timeout=30,
            ).json()
            report.check("写入 PowerContext 记忆", saved.get("source") == "powercontext", str(saved.get("error", ""))[:80])
            after = client.get(f"{args.base}/api/memory", timeout=20).json()
            report.check(
                "读回研究主题",
                (after.get("profile") or {}).get("topic") == "seekdb 内核开发验收",
                str((after.get("profile") or {}).get("topic")),
            )
            report.note(f"记忆条目：{before.get('entries', 0)} → {after.get('entries', 0)}")
            # 验收会改写研究主题；结束后恢复，避免影响演示数据
            if previous_profile.get("topic"):
                client.post(
                    f"{args.base}/api/memory/profile",
                    json={
                        "topic": previous_profile.get("topic", ""),
                        "style": previous_profile.get("style", "concise"),
                        "goal": previous_profile.get("goal", ""),
                    },
                    timeout=30,
                )

    ok = all(row[1] for row in report.rows)
    header = [
        "# 据典 · 端到端验收",
        "",
        f"- 时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 服务：{args.base}",
        f"- 结果：{'全部通过' if ok else '存在失败'}（{sum(1 for r in report.rows if r[1])}/{len(report.rows)}）",
        "",
        "```text",
        *report.lines,
        "```",
    ]
    path = LOG_DIR / f"verify-{stamp}.md"
    path.write_text("\n".join(header) + "\n", encoding="utf-8")
    print(f"\n日志：{path.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
