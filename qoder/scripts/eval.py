#!/usr/bin/env python3
"""固定题集评测：Recall@5、引用命中、拒答准确率与时延。"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.db import DBUnavailable, SeekDB  # noqa: E402
from app.embed import Embedder, EmbeddingError  # noqa: E402
from app.llm import LLMClient, answer_question  # noqa: E402


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def _text_of(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "") for key in ("text", "doc_title", "heading", "source")
    ).lower()


def _hit(rows: list[dict[str, Any]], keywords: list[str]) -> bool:
    lowered = [keyword.lower() for keyword in keywords if keyword]
    if not lowered:
        return False
    return any(any(keyword in _text_of(row) for keyword in lowered) for row in rows)


def _citation_hit(citations: list[dict[str, Any]], keywords: list[str]) -> bool:
    lowered = [keyword.lower() for keyword in keywords if keyword]
    if not lowered or not citations:
        return False
    for citation in citations:
        text = str(citation.get("text") or "").lower()
        if any(keyword in text for keyword in lowered):
            return True
    return False


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# 据典固定题集评测",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 向量模型：`{report['model']}`",
        f"- 语料：{report['corpus']['documents']} 篇 / {report['corpus']['chunks']} 片段",
        f"- 题集：{summary['answerable']} 道可答 + {summary['unanswerable']} 道资料外",
        "",
        "## 指标",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| Recall@{report['top_k']} | {summary['recall_at_5'] * 100:.0f}% "
        f"（{summary['recall_hits']}/{summary['answerable']}） |",
        f"| 引用命中率 | {summary['citation_hit'] * 100:.0f}% "
        f"（{summary['citation_hits']}/{summary['answerable']}） |",
        f"| 拒答准确率 | {summary['refusal_accuracy'] * 100:.0f}% "
        f"（{summary['refusal_hits']}/{summary['unanswerable']}） |",
        f"| 中位耗时 | {summary['p50_ms']} ms |",
        f"| 平均耗时 | {summary['mean_ms']} ms |",
        "",
        "## 逐题结果",
        "",
        "| # | 问题 | 类型 | 模式 | Recall@5 | 引用 | 拒答 | 耗时 |",
        "|---|---|---|---|---|---|---|---:|",
    ]
    for row in report["questions"]:
        kind = "可答" if row["answerable"] else "资料外"
        recall = "—" if not row["answerable"] else ("命中" if row["recall_at_5"] else "未命中")
        cite = "—" if not row["answerable"] else ("有" if row["citation_hit"] else "无")
        refusal = "—" if row["answerable"] else ("已拒答" if row["refused"] else "未拒答")
        question = str(row["question"]).replace("|", "\\|")
        lines.append(
            f"| {row['id']} | {question} | {kind} | {row['mode']} | {recall} | {cite} | "
            f"{refusal} | {row['elapsed_ms']} ms |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="据典固定题集评测")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题（调试用）")
    parser.add_argument("--ids", default=None, help="只跑指定 id，逗号分隔，例如 a01,u01")
    parser.add_argument("--no-llm", action="store_true", help="强制抽取式作答，不调用生成模型")
    args = parser.parse_args()

    settings = Settings.load()
    if args.no_llm:
        settings = replace(settings, llm_enabled=False)

    gold_path = settings.gold_path
    try:
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[FAIL] 读取 {gold_path} 失败：{exc}", file=sys.stderr)
        return 1
    questions = gold.get("questions") if isinstance(gold, dict) else None
    if not isinstance(questions, list) or not questions:
        print("[FAIL] data/gold.json 缺少 questions", file=sys.stderr)
        return 1
    if args.ids:
        wanted = {item.strip() for item in args.ids.split(",") if item.strip()}
        questions = [item for item in questions if item.get("id") in wanted]
    if args.limit:
        questions = questions[: args.limit]
    top_k = int(gold.get("top_k") or 5)

    db = SeekDB(settings)
    ok, detail = db.ping()
    if not ok:
        print(f"[FAIL] seekdb 连接失败：{detail}", file=sys.stderr)
        return 2
    try:
        stats = db.stats()
    except Exception as exc:
        print(f"[FAIL] 读取语料统计失败：{exc}", file=sys.stderr)
        return 2
    if stats["chunks"] <= 0:
        print("[FAIL] 语料为空，请先运行 .venv/bin/python scripts/seed.py", file=sys.stderr)
        return 2

    embedder = Embedder(settings)
    llm = LLMClient(settings)
    results: list[dict[str, Any]] = []
    latencies: list[int] = []

    print(
        f"评测 {len(questions)} 题 · Top-{top_k} · 语料 {stats['documents']} 篇 / "
        f"{stats['chunks']} 片段 · 模型 {settings.embed_model}"
    )
    for index, item in enumerate(questions, start=1):
        question = str(item.get("question") or "").strip()
        keywords = [str(keyword) for keyword in item.get("keywords") or [] if keyword]
        answerable = bool(item.get("answerable", True))
        started = time.perf_counter()
        try:
            vector = embedder.embed_query(question)
            retrieved = db.hybrid_search(question, vector, limit=settings.recall_limit)
            answer = answer_question(
                question,
                retrieved["fused"],
                retrieved["vector"],
                retrieved["fulltext"],
                settings=settings,
                llm=llm,
                corpus_contains=db.corpus_contains,
                profile=None,
                style="concise",
            )
        except EmbeddingError as exc:
            print(f"\n[FAIL] 第 {index} 题向量化失败：{exc}", file=sys.stderr)
            return 3
        except DBUnavailable as exc:
            print(f"\n[FAIL] 第 {index} 题检索失败：{exc}", file=sys.stderr)
            return 2
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        latencies.append(elapsed_ms)

        top_rows = retrieved["fused"][:top_k]
        recall_ok = answerable and _hit(top_rows, keywords)
        citation_ok = answerable and _citation_hit(answer.citations, keywords)
        refused = answer.mode == "insufficient"
        results.append(
            {
                "id": str(item.get("id") or index),
                "question": question,
                "answerable": answerable,
                "keywords": keywords,
                "mode": answer.mode,
                "confidence": round(answer.confidence, 4),
                "elapsed_ms": elapsed_ms,
                "recall_at_5": recall_ok,
                "citation_hit": citation_ok,
                "refused": refused,
                "reason": answer.reason,
                "answer": answer.answer,
                "citations": [
                    {
                        "n": citation.get("n"),
                        "chunk_id": citation.get("chunk_id"),
                        "doc_title": citation.get("doc_title"),
                        "heading": citation.get("heading"),
                    }
                    for citation in answer.citations
                ],
                "top_k": [
                    {
                        "rank": rank,
                        "chunk_id": row.get("id"),
                        "doc_title": row.get("doc_title"),
                        "heading": row.get("heading"),
                        "rrf": row.get("rrf"),
                    }
                    for rank, row in enumerate(top_rows, start=1)
                ],
            }
        )
        mark = "✓" if (recall_ok and citation_ok) or (not answerable and refused) else "×"
        print(
            f"  {mark} {item.get('id', index):>3} {answer.mode:<12} "
            f"{elapsed_ms:>5} ms  {question[:42]}"
        )

    answerable_rows = [row for row in results if row["answerable"]]
    unanswerable_rows = [row for row in results if not row["answerable"]]
    recall_hits = sum(1 for row in answerable_rows if row["recall_at_5"])
    citation_hits = sum(1 for row in answerable_rows if row["citation_hit"])
    refusal_hits = sum(1 for row in unanswerable_rows if row["refused"])
    summary = {
        "total": len(results),
        "answerable": len(answerable_rows),
        "unanswerable": len(unanswerable_rows),
        "recall_hits": recall_hits,
        "citation_hits": citation_hits,
        "refusal_hits": refusal_hits,
        "recall_at_5": recall_hits / len(answerable_rows) if answerable_rows else 0.0,
        "citation_hit": citation_hits / len(answerable_rows) if answerable_rows else 0.0,
        "refusal_accuracy": refusal_hits / len(unanswerable_rows) if unanswerable_rows else 0.0,
        "p50_ms": int(statistics.median(latencies)) if latencies else 0,
        "mean_ms": int(statistics.mean(latencies)) if latencies else 0,
    }
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": settings.embed_model,
        "llm_model": settings.llm_model if settings.llm_enabled else None,
        "top_k": top_k,
        "corpus": stats,
        "summary": summary,
        "questions": results,
    }

    out_dir = settings.evidence_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "eval.json"
    md_path = out_dir / "eval.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")

    print()
    print(
        f"Recall@{top_k} {summary['recall_at_5'] * 100:.0f}% · "
        f"引用命中 {summary['citation_hit'] * 100:.0f}% · "
        f"拒答准确 {summary['refusal_accuracy'] * 100:.0f}% · "
        f"p50 {summary['p50_ms']} ms · 均值 {summary['mean_ms']} ms"
    )
    print(f"已写入 {json_path.relative_to(ROOT)} 与 {md_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
