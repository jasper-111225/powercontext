#!/usr/bin/env node
/**
 * Seed the demo knowledge base through the real browser pipeline (so the
 * vectors are produced by the same INT8 model the app uses), then capture a
 * screenshot with the SQL panel open.
 *
 *   node scripts/seed-demo.mjs [baseUrl]
 */
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const base = process.argv[2] ?? "http://127.0.0.1:3000/";
const name = "示例知识库";

const browser = await chromium.launch({
  executablePath: process.env.BROWSER_PATH ?? "/usr/bin/microsoft-edge",
  headless: true,
  env: { ...process.env, HOME: process.env.BENCH_HOME ?? "/home/jasper/seekdb/.tmp_stepfun/home" },
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--enable-unsafe-webgpu", "--use-angle=swiftshader",
    "--disable-gpu-sandbox", "--disable-crash-reporter", "--disable-breakpad"],
});
const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
await page.goto(`${base}?backend=auto`, { waitUntil: "domcontentloaded" });
await page.waitForFunction(() => window.__sfkb?.getState().ready, null, { timeout: 300000 });
await page.waitForFunction(() => window.__sfkb.getState().samples > 0, null, { timeout: 30000 });

const result = await page.evaluate(async (kbName) => {
  const state = window.__sfkb.getState();
  const existing = state.kbs.find((kb) => kb.name === kbName);
  if (existing) {
    await window.__sfkb.selectKb(existing.id);
    if (existing.chunks > 0) return { id: existing.id, chunks: existing.chunks, reused: true };
    await window.__sfkb.loadSamples();
    return { id: existing.id, chunks: window.__sfkb.getState().chunks, reused: false };
  }
  const kb = await window.__sfkb.createKb(kbName);
  await window.__sfkb.loadSamples();
  return { id: kb.id, chunks: window.__sfkb.getState().chunks, reused: false };
}, name);
console.log("kb:", JSON.stringify(result));

await page.evaluate(async () => window.__sfkb.search("seekdb 如何把向量检索和全文检索融合起来？"));
await page.waitForSelector(".result", { timeout: 20000 });
await page.evaluate(() => window.scrollTo({ top: 0 }));
await page.waitForTimeout(600);

const shot = path.join(root, "evidence/screenshots/app-demo.png");
await page.screenshot({ path: shot, animations: "disabled", timeout: 20000 });

await page.getByRole("button", { name: /查看数据库内执行的 SQL/ }).click().catch(() => {});
await page.locator(".sql-card").scrollIntoViewIfNeeded().catch(() => {});
await page.waitForTimeout(500);
const sqlShot = path.join(root, "evidence/screenshots/app-demo-sql.png");
await page.screenshot({ path: sqlShot, animations: "disabled", timeout: 20000 });
console.log("screenshots:", shot, sqlShot);

await browser.close();
