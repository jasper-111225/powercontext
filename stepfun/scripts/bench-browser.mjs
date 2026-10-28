#!/usr/bin/env node
/**
 * Reproducible browser benchmark.
 *
 * Drives the real app in headless Edge/Chromium for each inference backend,
 * imports the sample corpus through the UI's own code path, then runs the
 * in-app benchmark (5 queries x 3 rounds) and stores the raw JSON plus a
 * screenshot under evidence/.
 *
 *   node scripts/bench-browser.mjs                # wasm + webgpu
 *   node scripts/bench-browser.mjs --only wasm
 */
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const base = process.env.APP_URL ?? "http://127.0.0.1:3000/";
const edge = process.env.BROWSER_PATH ?? "/usr/bin/microsoft-edge";
const onlyArg = process.argv.indexOf("--only");
const backends = onlyArg > 0 ? [process.argv[onlyArg + 1]] : ["wasm", "webgpu"];
const rounds = Number(process.env.BENCH_ROUNDS ?? 3);
const dtype = process.env.EMBED_DTYPE === "fp32" ? "fp32" : "int8";
const suffix = dtype === "fp32" ? "-fp32" : "";

const evidenceDir = path.join(root, "evidence");
const shotDir = path.join(evidenceDir, "screenshots");
await fs.mkdir(shotDir, { recursive: true });

const results = [];

function summarize(bench, warmBoot) {
  return {
    backend: bench.backend,
    dtype: bench.dtype,
    modelLoadMs: bench.modelLoadMs,
    warmModelLoadMs: warmBoot?.modelLoadMs ?? null,
    warmupMs: bench.warmupMs,
    docChunksPerSec: bench.docEmbed.chunksPerSec,
    docCharsPerSec: bench.docEmbed.charsPerSec,
    queryEmbedP50Ms: bench.queryEmbedP50Ms,
    dbSearchP50Ms: bench.dbSearchP50Ms,
    e2eP50Ms: bench.e2eP50Ms,
    e2eP95Ms: bench.e2eP95Ms,
    crossOriginIsolated: bench.environment.crossOriginIsolated,
    adapter: bench.environment.adapter,
    samples: bench.queries * bench.rounds,
  };
}

for (const backend of backends) {
  const browser = await chromium.launch({
    executablePath: edge,
    headless: true,
    env: { ...process.env, HOME: process.env.BENCH_HOME ?? "/home/jasper/seekdb/.tmp_stepfun/home" },
    args: [
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--enable-unsafe-webgpu",
      "--use-angle=swiftshader",
      "--disable-gpu-sandbox",
      "--disable-crash-reporter",
      "--disable-breakpad",
    ],
  });
  const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().includes("VerifyEachNodeIsAssignedToAnEp")) {
      errors.push(message.text().slice(0, 300));
    }
  });

  console.log(`\n=== ${backend} ===`);
  await page.goto(`${base}?backend=${backend}&dtype=${dtype}`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForFunction(
    () => {
      const state = window.__sfkb?.getState();
      return state && state.embedder.status !== "loading";
    },
    null,
    { timeout: 300000 },
  );
  const boot = await page.evaluate(() => window.__sfkb.getState().embedder);
  if (boot.status !== "ready") {
    console.error("embedder failed:", JSON.stringify(boot), errors);
    await browser.close();
    throw new Error(`${backend} 初始化失败：${boot.error}`);
  }
  console.log("ready:", JSON.stringify({ backend: boot.backend, load: boot.modelLoadMs, warmup: boot.warmupMs }));

  await page.waitForFunction(() => window.__sfkb.getState().samples > 0, null, { timeout: 30000 });

  const ingest = await page.evaluate(async (name) => {
    const state = window.__sfkb.getState();
    const old = state.kbs.find((kb) => kb.name === name);
    if (old) await window.__sfkb.selectKb(old.id);
    else await window.__sfkb.createKb(name);
    const started = performance.now();
    const commit = await window.__sfkb.loadSamples();
    return { commit, wallMs: performance.now() - started, lastIngest: window.__sfkb.getState().lastIngest };
  }, `基准-${backend}`);
  console.log("ingest:", JSON.stringify(ingest.lastIngest));

  const bench = await page.evaluate(async (r) => window.__sfkb.runBench(r), rounds);
  console.log("bench:", JSON.stringify(summarize(bench)));

  const showcase = await page.evaluate(async () => window.__sfkb.search("seekdb 如何把向量检索和全文检索融合起来？"));
  await page.waitForSelector(".result", { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(600);
  const shot = path.join(shotDir, `app-${backend}${suffix}.png`);
  await page.screenshot({ path: shot, animations: "disabled", timeout: 20000 });
  console.log("search:", JSON.stringify({ engine: showcase?.engine, dbMs: showcase?.dbMs, results: showcase?.results?.length, sql: showcase?.sql?.length }));

  // Second load in the same profile: HTTP cache and compiled shaders are warm.
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => window.__sfkb && window.__sfkb.getState().ready, null, { timeout: 240000 });
  const warmBoot = await page.evaluate(() => window.__sfkb.getState().embedder);
  console.log("warm boot:", JSON.stringify({ load: warmBoot.modelLoadMs, warmup: warmBoot.warmupMs }));

  const payload = {
    generatedAt: new Date().toISOString(),
    app: base,
    browser: await page.evaluate(() => navigator.userAgent),
    backend,
    boot: {
      backend: boot.backend,
      dtype: boot.dtype,
      modelLoadMs: boot.modelLoadMs,
      warmupMs: boot.warmupMs,
      adapter: boot.adapter,
    },
    warmBoot: {
      backend: warmBoot.backend,
      modelLoadMs: warmBoot.modelLoadMs,
      warmupMs: warmBoot.warmupMs,
    },
    ingest,
    bench,
    showcase: {
      query: showcase?.query,
      engine: showcase?.engine,
      dbMs: showcase?.dbMs,
      sql: showcase?.sql,
      results: showcase?.results?.slice(0, 5),
    },
    errors,
  };
  const file = path.join(evidenceDir, `perf-${backend}${suffix}.json`);
  await fs.writeFile(file, JSON.stringify(payload, null, 2), "utf8");
  results.push(summarize(bench, warmBoot));

  await page.evaluate(async (name) => {
    const state = window.__sfkb.getState();
    const kb = state.kbs.find((item) => item.name === name);
    if (kb) {
      await fetch(`/api/kbs/${kb.id}`, { method: "DELETE" });
    }
  }, `基准-${backend}`).catch(() => {});

  await browser.close();
}

if (results.length > 1) {
  const wasm = results.find((item) => item.backend === "wasm");
  const gpu = results.find((item) => item.backend === "webgpu");
  const summary = {
    generatedAt: new Date().toISOString(),
    rows: results,
    comparison:
      wasm && gpu
        ? {
            docThroughputSpeedup: Number((gpu.docChunksPerSec / wasm.docChunksPerSec).toFixed(2)),
            queryEmbedSpeedup: Number((wasm.queryEmbedP50Ms / gpu.queryEmbedP50Ms).toFixed(2)),
            e2eSpeedup: Number((wasm.e2eP50Ms / gpu.e2eP50Ms).toFixed(2)),
          }
        : null,
  };
  await fs.writeFile(path.join(evidenceDir, "perf-summary.json"), JSON.stringify(summary, null, 2), "utf8");
  console.log("\nsummary:", JSON.stringify(summary.comparison));
}

console.log("\nfiles:");
for (const backend of backends) console.log(`  evidence/perf-${backend}.json`);
