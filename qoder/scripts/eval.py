#!/usr/bin/env python
"""跑固定评测集：Recall@5、引用命中率、拒答准确率、平均耗时。

  python scripts/eval.py              # 完整链路（检索 + 生成/摘录）
  python scripts/eval.py --no-llm     # 只跑检索 + 抽取式，快速自查
  python scripts/eval.py --limit 5    # 冒烟
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.embed import Embedder  # noqa: E402
from app.llm import Answerer  # noqa: E402


def doc_matches(source: str, expected: str) -> bool:
    return bool(expected) and Path(source).name == expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", default=str(ROOT / "data" / "gold.json"))
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    cases = gold["cases"][: args.limit] if args.limit else gold["cases"]
    embedder = Embedder()
    answerer = Answerer()
    conn = db.connect()
    results = []
    latencies = []
    try:
        corpus_text = db.corpus_text(conn)
        for index, case in enumerate(cases, start=1):
            started = time.perf_counter()
            vector = embedder.embed_one(case["question"])
            search = db.hybrid_search(conn, case["question"], vector)
            evidence = search["fused"]
            top_sim = max((item.get("vec_sim") or 0) for item in evidence) if evidence else 0.0
            if args.no_llm:
                answer = answerer._extractive(case["question"], evidence, top_sim)
            else:
                answer = answerer.answer(case["question"], evidence, corpus_text=corpus_text)
            elapsed = (time.perf_counter() - started) * 1000
            latencies.append(elapsed)

            top5 = evidence[:5]
            recall_hit = any(doc_matches(item["source"], case["expect_doc"]) for item in top5)
            cited_docs = [
                evidence[i - 1]["source"]
                for i in answer.citations
                if 1 <= i <= len(evidence)
            ]
            citation_hit = any(doc_matches(source, case["expect_doc"]) for source in cited_docs)
            refused = answer.mode == "insufficient"
            correct = (refused if not case["answerable"] else (not refused))

            row = {
                "id": case["id"],
                "question": case["question"],
                "expected": case["expect_doc"],
                "answerable": case["answerable"],
                "recall_hit": recall_hit,
                "citation_hit": citation_hit,
                "refused": refused,
                "correct": correct,
                "mode": answer.mode,
                "confidence": round(top_sim, 3),
                "latency_ms": int(elapsed),
                "top5": [Path(item["source"]).name for item in top5],
                "cited": [Path(source).name for source in cited_docs],
                "answer": answer.text[:280],
            }
            results.append(row)
            mark = "✓" if correct and (recall_hit or not case["answerable"]) else "✗"
            print(
                f"[{index:2d}/{len(cases)}] {mark} {case['id']} {answer.mode:12s} "
                f"R@5={'Y' if recall_hit else 'N'} cite={'Y' if citation_hit else 'N'} "
                f"{row['latency_ms']:5d}ms  {case['question'][:36]}",
                flush=True,
            )

        answerable = [r for r in results if r["answerable"]]
        unanswerable = [r for r in results if not r["answerable"]]
        metrics = {
            "recall_at_5": round(sum(r["recall_hit"] for r in answerable) / max(1, len(answerable)), 4),
            "citation_hit": round(sum(r["citation_hit"] for r in answerable) / max(1, len(answerable)), 4),
            "refusal_accuracy": round(
                sum(r["refused"] for r in unanswerable) / max(1, len(unanswerable)), 4
            ),
            "answer_accuracy": round(
                sum(r["correct"] for r in answerable) / max(1, len(answerable)), 4
            ),
            "avg_latency_ms": int(statistics.mean(latencies)),
            "p50_latency_ms": int(statistics.median(latencies)),
            "total": len(results),
            "answerable": len(answerable),
            "unanswerable": len(unanswerable),
            "corpus_chunks": db.corpus_stats(conn)["chunks"],
        }
        payload = {
            "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": settings.llm_model,
            "embed_model": settings.embed_model,
            "mode": "extractive" if args.no_llm else "full",
            "metrics": metrics,
            "cases": results,
        }
        out = ROOT / "docs" / "evidence" / "eval.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        lines = [
            "# 评测结果",
            "",
            f"- 运行时间：{payload['run_at']}",
            f"- 生成模型：`{payload['model']}`；向量模型：`{payload['embed_model']}`；模式：`{payload['mode']}`",
            f"- 语料：{metrics['corpus_chunks']} 个片段；题目：{metrics['total']}（可答 {metrics['answerable']} / 不可答 {metrics['unanswerable']}）",
            "",
            "| 指标 | 数值 |",
            "|---|---:|",
            f"| Recall@5 | {metrics['recall_at_5']:.0%} |",
            f"| 引用命中率 | {metrics['citation_hit']:.0%} |",
            f"| 拒答准确率 | {metrics['refusal_accuracy']:.0%} |",
            f"| 中位耗时（p50） | {metrics['p50_latency_ms']} ms |",
            f"| 平均耗时 | {metrics['avg_latency_ms']} ms（含一次模型重载） |",
            "",
            "| # | 问题 | 期望来源 | Top-5 | 引用 | 模式 |",
            "|---|---|---|---|---|---|",
        ]
        for row in results:
            lines.append(
                f"| {row['id']} | {row['question']} | {row['expected'] or '—'} | "
                f"{'✓' if row['recall_hit'] else '✗'} | {'✓' if row['citation_hit'] else '✗'} | {row['mode']} |"
            )
        (ROOT / "docs" / "evidence" / "eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        print(f"写出 {out.relative_to(ROOT)} 与 docs/evidence/eval.md")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
