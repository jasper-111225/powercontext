#!/usr/bin/env python3
"""Offline retrieval evaluation for FP32 vs INT8.

Reads the chunk set produced by `node scripts/build-corpus.mjs` and the labelled
queries in data/eval/queries.json, encodes everything with the same ONNX files
the browser uses (CLS pooling + L2 normalisation, BGE query prefix), and reports
Recall@K / nDCG@K / MRR at document level.

Usage:
    .venv/bin/python scripts/eval_retrieval.py [--models fp32,int8] [--prefix-ablation]
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / ".models" / "bge-small-zh-v1.5"
PREFIX = ""
INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

MODEL_FILES = {
    "fp32": MODEL_DIR / "onnx" / "model.onnx",
    "int8": MODEL_DIR / "onnx" / "model_int8_ours.onnx",
}


def load_vectors(session: ort.InferenceSession, tokenizer: Tokenizer, texts: list[str], max_length: int = 512, batch: int = 16) -> np.ndarray:
    out: list[np.ndarray] = []
    names = {i.name for i in session.get_inputs()}
    for start in range(0, len(texts), batch):
        encs = [tokenizer.encode(t) for t in texts[start : start + batch]]
        width = max(1, min(max_length, max(len(e.ids) for e in encs)))
        ids = np.zeros((len(encs), width), dtype=np.int64)
        mask = np.zeros((len(encs), width), dtype=np.int64)
        types = np.zeros((len(encs), width), dtype=np.int64)
        for row, enc in enumerate(encs):
            n = min(width, len(enc.ids))
            ids[row, :n] = enc.ids[:n]
            mask[row, :n] = enc.attention_mask[:n]
            types[row, :n] = enc.type_ids[:n]
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in names:
            feeds["token_type_ids"] = types
        hidden = session.run(None, feeds)[0]
        cls = hidden[:, 0, :].astype(np.float32)
        cls /= np.linalg.norm(cls, axis=1, keepdims=True) + 1e-12
        out.append(cls)
    return np.concatenate(out, axis=0)


def dcg(gains: list[int]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def evaluate(ranked_docs: list[str], relevant: set[str], k: int) -> dict:
    top = ranked_docs[:k]
    hits = [1 if doc in relevant else 0 for doc in top]
    ideal = [1] * min(len(relevant), k)
    ndcg = dcg(hits) / dcg(ideal) if ideal else 0.0
    recall = sum(hits) / len(relevant) if relevant else 0.0
    return {"recall": recall, "ndcg": ndcg, "hits": hits}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="fp32,int8")
    ap.add_argument("--out", default="evidence/quality-report.json")
    ap.add_argument("--prefix-ablation", action="store_true")
    args = ap.parse_args()

    chunks_payload = json.loads((ROOT / "data/eval/chunks.json").read_text(encoding="utf-8"))
    queries = json.loads((ROOT / "data/eval/queries.json").read_text(encoding="utf-8"))
    chunks = chunks_payload["chunks"]

    doc_id_by_name = {doc["docName"]: doc["docId"] for doc in chunks_payload["documents"]}
    chunk_doc_ids = [c["docId"] for c in chunks]
    unique_docs = list(dict.fromkeys(chunk_doc_ids))
    doc_index = {doc_id: i for i, doc_id in enumerate(unique_docs)}
    chunk_doc_index = np.array([doc_index[d] for d in chunk_doc_ids])

    tokenizer = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    report: dict = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "docs": chunks_payload["documents"],
            "chunks": len(chunks),
            "chars": chunks_payload["charCount"],
            "queries": len(queries),
            "pooling": "cls",
            "normalized": True,
            "queryPrefix": PREFIX or "(none)",
            "maxLength": 512,
        },
        "models": {},
    }

    query_texts = [q["query"] for q in queries]
    results: dict[str, dict] = {}

    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        path = MODEL_FILES.get(name)
        if not path or not path.exists():
            print(f"skip {name}: {path} missing")
            continue
        started = time.perf_counter()
        session = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        load_ms = (time.perf_counter() - started) * 1000

        t0 = time.perf_counter()
        chunk_vecs = load_vectors(session, tokenizer, [c["text"] for c in chunks])
        query_vecs = load_vectors(session, tokenizer, [PREFIX + q for q in query_texts])
        encode_ms = (time.perf_counter() - t0) * 1000

        sims = query_vecs @ chunk_vecs.T  # [queries, chunks]
        per_query = []
        for qi, query in enumerate(queries):
            scores = sims[qi]
            doc_scores = np.full(len(unique_docs), -np.inf, dtype=np.float32)
            np.maximum.at(doc_scores, chunk_doc_index, scores)
            order = np.argsort(-doc_scores)
            ranked_docs = [unique_docs[i] for i in order]
            relevant_ids = {doc_id_by_name[name_] for name_ in query["relevant"] if name_ in doc_id_by_name}
            metrics = {}
            for k in (1, 5, 10):
                metrics[f"recall@{k}"] = evaluate(ranked_docs, relevant_ids, k)["recall"]
                metrics[f"ndcg@{k}"] = evaluate(ranked_docs, relevant_ids, k)["ndcg"]
            first_hit = next((i + 1 for i, d in enumerate(ranked_docs) if d in relevant_ids), None)
            metrics["mrr"] = 1.0 / first_hit if first_hit else 0.0
            top5 = [
                {
                    "doc": next((d["docName"] for d in chunks_payload["documents"] if d["docId"] == ranked_docs[i]), ranked_docs[i]),
                    "score": round(float(doc_scores[order[i]]), 4),
                    "relevant": ranked_docs[i] in relevant_ids,
                }
                for i in range(min(5, len(order)))
            ]
            per_query.append({"id": query["id"], "query": query["query"], "metrics": metrics, "top5": top5})

        aggregate = {}
        for key in ("recall@1", "recall@5", "recall@10", "ndcg@5", "ndcg@10", "mrr"):
            values = [q["metrics"][key] for q in per_query]
            aggregate[key] = {
                "mean": round(float(np.mean(values)), 4),
                "min": round(float(np.min(values)), 4),
            }

        results[name] = {
            "file": str(path.relative_to(ROOT)),
            "bytes": path.stat().st_size,
            "loadMs": round(load_ms, 1),
            "encodeMs": round(encode_ms, 1),
            "aggregate": aggregate,
            "perQuery": per_query,
        }
        report["models"][name] = {
            "file": results[name]["file"],
            "bytes": results[name]["bytes"],
            "loadMs": results[name]["loadMs"],
            "encodeMs": results[name]["encodeMs"],
            "aggregate": aggregate,
        }
        print(f"{name:6s} " + "  ".join(f"{k}={v['mean']:.3f}" for k, v in aggregate.items()))

    if "fp32" in results and "int8" in results:
        fp32 = results["fp32"]
        int8 = results["int8"]
        deltas = {}
        for key in fp32["aggregate"]:
            deltas[key] = {
                "absolute": round(int8["aggregate"][key]["mean"] - fp32["aggregate"][key]["mean"], 4),
                "relativePct": round(
                    100 * (int8["aggregate"][key]["mean"] - fp32["aggregate"][key]["mean"]) / max(1e-9, fp32["aggregate"][key]["mean"]),
                    2,
                ),
            }
        report["comparison"] = {
            "baseline": "fp32",
            "candidate": "int8",
            "deltas": deltas,
            "compression": round(fp32["bytes"] / int8["bytes"], 2),
            "sizeMB": {"fp32": round(fp32["bytes"] / 1e6, 2), "int8": round(int8["bytes"] / 1e6, 2)},
        }
        print("int8 - fp32:", {k: v["absolute"] for k, v in deltas.items()})

    if args.prefix_ablation and "fp32" in MODEL_FILES:
        session = ort.InferenceSession(str(MODEL_FILES["fp32"]), so, providers=["CPUExecutionProvider"])
        chunk_vecs = load_vectors(session, tokenizer, [c["text"] for c in chunks])
        ablation = {}
        for label, prefix in (("no_prefix", PREFIX), ("with_instruction", INSTRUCTION)):
            query_vecs = load_vectors(session, tokenizer, [prefix + q for q in query_texts])
            sims = query_vecs @ chunk_vecs.T
            recalls, ndcgs = [], []
            for qi, query in enumerate(queries):
                doc_scores = np.full(len(unique_docs), -np.inf, dtype=np.float32)
                np.maximum.at(doc_scores, chunk_doc_index, sims[qi])
                order = np.argsort(-doc_scores)
                ranked = [unique_docs[i] for i in order]
                relevant_ids = {doc_id_by_name[n] for n in query["relevant"] if n in doc_id_by_name}
                recalls.append(evaluate(ranked, relevant_ids, 5)["recall"])
                ndcgs.append(evaluate(ranked, relevant_ids, 5)["ndcg"])
            ablation[label] = {"recall@5": round(float(np.mean(recalls)), 4), "ndcg@5": round(float(np.mean(ndcgs)), 4)}
        report["prefixAblation"] = ablation
        print("prefix ablation:", ablation)

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"-> {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
