#!/usr/bin/env node
/**
 * A/B measurement for the batching strategy, inside the real browser runtime.
 *
 *   fixed : contiguous batches of 8 texts
 *   token : length-sorted, token-budget batches (the default)
 *
 * Both modes run in the same page, same model, same texts, alternating rounds.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const chunks = JSON.parse(await fs.readFile(path.join(root, "data/eval/chunks.json"), "utf8")).chunks;
const texts = chunks.slice(0, 24).map((chunk) => chunk.text);
const rounds = Number(process.env.AB_ROUNDS ?? 2);

const benchHome = process.env.BENCH_HOME ?? path.join(root, ".bench-home");
await fs.mkdir(benchHome, { recursive: true });

const browser = await chromium.launch({
  executablePath: process.env.BROWSER_PATH ?? "/usr/bin/microsoft-edge",
  headless: true,
  env: { ...process.env, HOME: benchHome },
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--enable-unsafe-webgpu", "--use-angle=swiftshader",
    "--disable-gpu-sandbox", "--disable-crash-reporter", "--disable-breakpad"],
});
const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
await page.goto(process.env.APP_URL ?? "http://127.0.0.1:3000/?backend=wasm", { waitUntil: "domcontentloaded" });
await page.waitForFunction(() => window.__sfkb?.getState().ready, null, { timeout: 240000 });
const boot = await page.evaluate(() => window.__sfkb.getState().embedder);
await page.evaluate(async () => window.__sfkb.embedDocs(["预热文本，用于初始化推理缓冲区。"], "token"));

const samples = { fixed: [], token: [] };
for (let round = 0; round < rounds; round += 1) {
  for (const mode of round % 2 === 0 ? ["fixed", "token"] : ["token", "fixed"]) {
    const started = Date.now();
    await page.evaluate(async ({ texts: input, mode: m }) => window.__sfkb.embedDocs(input, m), { texts, mode });
    samples[mode].push(Date.now() - started);
  }
}

const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

const summary = Object.fromEntries(
  Object.entries(samples).map(([mode, values]) => [
    mode,
    {
      runsMs: values,
      medianMs: median(values),
      chunksPerSec: Number((texts.length / (median(values) / 1000)).toFixed(2)),
      charsPerSec: Number((texts.reduce((sum, text) => sum + text.length, 0) / (median(values) / 1000)).toFixed(0)),
    },
  ]),
);
const payload = {
  generatedAt: new Date().toISOString(),
  backend: boot.backend,
  dtype: boot.dtype,
  texts: texts.length,
  chars: texts.reduce((sum, text) => sum + text.length, 0),
  rounds,
  summary,
  speedup: Number((summary.token.chunksPerSec / summary.fixed.chunksPerSec).toFixed(2)),
};
await fs.mkdir(path.join(root, "evidence"), { recursive: true });
await fs.writeFile(path.join(root, "evidence/batch-ab.json"), JSON.stringify(payload, null, 2), "utf8");
console.log(JSON.stringify(payload, null, 2));
await browser.close();
