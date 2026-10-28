#!/usr/bin/env python3
"""FP32 -> INT8 / FP16 quantization for the browser embedding model.

INT8: ONNX Runtime dynamic quantization (weights QUInt8, per-channel,
activations computed at runtime). This is the model shipped to the browser for
both the WASM and the WebGPU execution providers.

The quantized file is validated against the FP32 baseline with a fixed sentence
set: cosine similarity of the sentence embeddings plus max absolute error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import onnxruntime as ort
from onnxruntime.quantization import QuantType, quantize_dynamic
from tokenizers import Tokenizer

SENTENCES = [
    "seekdb 是 OceanBase 推出的 AI 原生数据库，支持向量检索与全文检索。",
    "在浏览器里用 WebGPU 运行量化后的 embedding 模型。",
    "INT8 量化把模型体积压缩到原来的四分之一。",
    "Hybrid search fuses full-text and vector ranking with RRF.",
    "端侧推理不上传用户文档，隐私数据留在本地。",
    "HNSW 索引可以加速高维向量的近邻查询。",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def embed(session: ort.InferenceSession, tokenizer: Tokenizer, texts: list[str], max_length: int = 256) -> np.ndarray:
    encs = [tokenizer.encode(t) for t in texts]
    seqs = [(e.ids[:max_length], e.attention_mask[:max_length], e.type_ids[:max_length]) for e in encs]
    width = max(len(s[0]) for s in seqs)
    ids = np.zeros((len(seqs), width), dtype=np.int64)
    mask = np.zeros((len(seqs), width), dtype=np.int64)
    types = np.zeros((len(seqs), width), dtype=np.int64)
    for row, (i, m, t) in enumerate(seqs):
        ids[row, : len(i)] = i
        mask[row, : len(m)] = m
        types[row, : len(t)] = t
    feeds = {"input_ids": ids, "attention_mask": mask}
    names = {i.name for i in session.get_inputs()}
    if "token_type_ids" in names:
        feeds["token_type_ids"] = types
    hidden = session.run(None, feeds)[0]
    # BGE uses CLS pooling + L2 normalization.
    cls = hidden[:, 0, :].astype(np.float32)
    return cls / np.linalg.norm(cls, axis=1, keepdims=True)


def cosine(a: np.ndarray, b: np.ndarray) -> list[float]:
    return [float(x) for x in np.sum(a * b, axis=1)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=".models/bge-small-zh-v1.5/onnx/model.onnx")
    ap.add_argument("--tokenizer", default=".models/bge-small-zh-v1.5/tokenizer.json")
    ap.add_argument("--outdir", default=".models/bge-small-zh-v1.5/onnx")
    ap.add_argument("--report", default="evidence/model-quantization.json")
    args = ap.parse_args()

    src = Path(args.src)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    int8_path = outdir / "model_int8_ours.onnx"

    report: dict = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceModel": {
            "file": str(src),
            "bytes": src.stat().st_size,
            "sha256": sha256(src),
        },
        "quantization": {
            "int8": {
                "tool": "onnxruntime.quantization.quantize_dynamic",
                "weightType": "QUInt8",
                "perChannel": True,
                "activations": "dynamic (computed at runtime)",
                "file": str(int8_path),
            },

        },
    }

    t0 = time.perf_counter()
    quantize_dynamic(
        model_input=str(src),
        model_output=str(int8_path),
        weight_type=QuantType.QUInt8,
        per_channel=True,
        reduce_range=False,
    )
    report["quantization"]["int8"]["elapsedSec"] = round(time.perf_counter() - t0, 2)

    # Numeric validation against the fp32 baseline.
    tokenizer = Tokenizer.from_file(args.tokenizer)
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    base = ort.InferenceSession(str(src), so, providers=["CPUExecutionProvider"])
    base_vec = embed(base, tokenizer, SENTENCES)

    for label, path, key in [("int8", int8_path, "int8")]:
        info = {
            "file": str(path),
            "bytes": path.stat().st_size,
            "compressionRatio": round(src.stat().st_size / path.stat().st_size, 2),
            "sha256": sha256(path),
        }
        try:
            sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
            vec = embed(sess, tokenizer, SENTENCES)
            cos = cosine(base_vec, vec)
            info["validation"] = {
                "ok": True,
                "sentences": len(SENTENCES),
                "cosineMean": round(float(np.mean(cos)), 6),
                "cosineMin": round(float(np.min(cos)), 6),
                "maxAbsError": round(float(np.max(np.abs(base_vec - vec))), 6),
                "perSentenceCosine": [round(c, 6) for c in cos],
            }
        except Exception as exc:  # pragma: no cover - depends on ORT build
            info["validation"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        report["quantization"][key].update(info)

    report["summary"] = {
        "fp32MB": round(report["sourceModel"]["bytes"] / 1e6, 2),
        "int8MB": round(int8_path.stat().st_size / 1e6, 2),
        "int8Compression": round(src.stat().st_size / int8_path.stat().st_size, 2),
    }

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    for label in ("int8",):
        v = report["quantization"][label]["validation"]
        print(label, "validation:", v.get("cosineMean"), v.get("cosineMin"), v.get("error", ""))


if __name__ == "__main__":
    main()
