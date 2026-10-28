#!/usr/bin/env node
/**
 * Download the FP32 ONNX export + tokenizer needed to reproduce the INT8
 * quantization. Defaults to the ModelScope mirror (reachable from mainland
 * China); set MODEL_MIRROR=hf to use huggingface.co instead.
 *
 * The browser only needs web/public/models/.../onnx/model_int8.onnx, which is
 * committed. You only need this script if you want to re-run quantization.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { createWriteStream } from "node:fs";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repo = "Xenova/bge-small-zh-v1.5";
const mirror = process.env.MODEL_MIRROR ?? "modelscope";
const base =
  mirror === "hf"
    ? `https://huggingface.co/${repo}/resolve/main`
    : `https://www.modelscope.cn/models/${repo}/resolve/master`;

const destDir = path.join(root, ".models", "bge-small-zh-v1.5");
const files = [
  "config.json",
  "tokenizer.json",
  "tokenizer_config.json",
  "special_tokens_map.json",
  "vocab.txt",
  "onnx/model.onnx",
];

async function download(relative) {
  const target = path.join(destDir, relative);
  await fs.mkdir(path.dirname(target), { recursive: true });
  const existing = await fs.stat(target).catch(() => null);
  if (existing && existing.size > 0) {
    console.log(`skip ${relative} (${(existing.size / 1e6).toFixed(1)} MB)`);
    return;
  }
  const url = `${base}/${relative}`;
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok || !response.body) throw new Error(`${relative}: HTTP ${response.status}`);
  const total = Number(response.headers.get("content-length") ?? 0);
  console.log(`fetch ${relative} (${total ? (total / 1e6).toFixed(1) + " MB" : "?"})`);
  await pipeline(Readable.fromWeb(response.body), createWriteStream(target));
  const stat = await fs.stat(target);
  console.log(`  -> ${(stat.size / 1e6).toFixed(1)} MB`);
}

for (const file of files) {
  await download(file);
}

console.log(`
模型已下载到 .models/bge-small-zh-v1.5
下一步：
  .venv/bin/python scripts/quantize.py     # 生成 INT8 并写入 evidence/model-quantization.json
  cp .models/bge-small-zh-v1.5/onnx/model_int8_ours.onnx web/public/models/bge-small-zh-v1.5/onnx/model_int8.onnx
`);
