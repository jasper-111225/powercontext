import fs from "node:fs/promises";
import path from "node:path";

import express from "express";

import config from "./config.mjs";
import { buildChunks, sanitizeQuery } from "../shared/chunk.mjs";
import * as db from "./db.mjs";

const app = express();
app.disable("x-powered-by");
app.use(express.json({ limit: "64mb" }));

// Cross-origin isolation enables SharedArrayBuffer, which unlocks multi-threaded
// ORT WASM inference. Every asset is served from this origin.
app.use((req, res, next) => {
  res.setHeader("Cross-Origin-Opener-Policy", "same-origin");
  res.setHeader("Cross-Origin-Embedder-Policy", "require-corp");
  res.setHeader("Cross-Origin-Resource-Policy", "same-origin");
  next();
});

app.use((req, res, next) => {
  const started = performance.now();
  res.on("finish", () => {
    if (req.path.startsWith("/api")) {
      console.log(`${req.method} ${req.originalUrl} ${res.statusCode} ${(performance.now() - started).toFixed(1)}ms`);
    }
  });
  next();
});

const wrap = (handler) => (req, res, next) => Promise.resolve(handler(req, res, next)).catch(next);

app.get("/api/health", wrap(async (req, res) => {
  res.json({
    ok: true,
    seekdb: await db.health(),
    model: config.model,
    limits: config.limits,
    time: Date.now(),
  });
}));

app.get("/api/kbs", wrap(async (req, res) => {
  res.json({ kbs: await db.listKbs() });
}));

app.post("/api/kbs", wrap(async (req, res) => {
  const kb = await db.createKb(req.body ?? {});
  res.status(201).json({ kb });
}));

app.get("/api/kbs/:id", wrap(async (req, res) => {
  res.json({ kb: await db.kbStats(req.params.id) });
}));

app.patch("/api/kbs/:id", wrap(async (req, res) => {
  await db.renameKb(req.params.id, req.body?.name);
  res.json({ kb: await db.kbStats(req.params.id) });
}));

app.delete("/api/kbs/:id", wrap(async (req, res) => {
  await db.deleteKb(req.params.id);
  res.json({ ok: true });
}));

app.get("/api/kbs/:id/schema", wrap(async (req, res) => {
  res.json(await db.schemaEvidence(req.params.id));
}));

app.get("/api/samples", wrap(async (req, res) => {
  const entries = await fs.readdir(config.corpusDir).catch(() => []);
  const samples = [];
  for (const name of entries.sort()) {
    if (!/\.(md|txt|markdown)$/i.test(name)) continue;
    const text = await fs.readFile(path.join(config.corpusDir, name), "utf8");
    samples.push({ name, text, chars: text.length });
  }
  res.json({ samples, totalChars: samples.reduce((sum, s) => sum + s.chars, 0) });
}));

app.post("/api/ingest/prepare", wrap(async (req, res) => {
  const { kbId, files } = req.body ?? {};
  db.assertKbId(kbId);
  if (!Array.isArray(files) || files.length === 0) {
    res.status(400).json({ error: "files 不能为空" });
    return;
  }
  if (files.length > config.limits.maxFiles) {
    res.status(400).json({ error: `一次最多导入 ${config.limits.maxFiles} 个文件` });
    return;
  }
  const chunks = [];
  const perFile = [];
  for (const file of files) {
    const name = String(file?.name ?? "untitled.txt").slice(0, 200);
    const text = String(file?.text ?? "");
    if (!text.trim()) continue;
    if (Buffer.byteLength(text, "utf8") > config.limits.maxFileBytes) {
      res.status(400).json({ error: `${name} 超过 ${config.limits.maxFileBytes / 1024 / 1024}MB` });
      return;
    }
    const docChunks = buildChunks(name, text).slice(0, config.limits.maxChunksPerDoc);
    chunks.push(...docChunks.map((c) => ({ ...c, source: file?.source === "sample" ? "sample" : "upload" })));
    perFile.push({ name, chars: text.length, chunks: docChunks.length });
    if (chunks.length > config.limits.maxChunksPerImport) {
      res.status(400).json({ error: `单次导入分块数超过 ${config.limits.maxChunksPerImport}` });
      return;
    }
  }
  res.json({ kbId, files: perFile, chunks, totalChunks: chunks.length, totalChars: chunks.reduce((sum, c) => sum + c.chars, 0) });
}));

app.post("/api/ingest/commit", wrap(async (req, res) => {
  const { kbId, items } = req.body ?? {};
  db.assertKbId(kbId);
  if (!Array.isArray(items) || items.length === 0) {
    res.status(400).json({ error: "items 不能为空" });
    return;
  }
  if (items.length > config.limits.maxChunksPerImport) {
    res.status(400).json({ error: "提交分块数超限" });
    return;
  }
  for (const item of items) {
    if (!item?.id || typeof item.text !== "string") {
      res.status(400).json({ error: "分块缺少 id/text" });
      return;
    }
    if (!Array.isArray(item.embedding) || item.embedding.length !== config.model.dim) {
      res.status(400).json({ error: `分块 ${item.id} 向量维度不是 ${config.model.dim}` });
      return;
    }
    if (!item.embedding.every((v) => Number.isFinite(v))) {
      res.status(400).json({ error: `分块 ${item.id} 向量含非法数值` });
      return;
    }
  }
  const result = await db.upsertChunks(kbId, items);
  res.json({ ok: true, ...result });
}));

app.post("/api/search", wrap(async (req, res) => {
  const { kbId, query, embedding, mode = "hybrid", topK = 8 } = req.body ?? {};
  db.assertKbId(kbId);
  if (!String(query ?? "").trim()) {
    res.status(400).json({ error: "查询不能为空" });
    return;
  }
  const modeSafe = ["hybrid", "vector", "fulltext"].includes(mode) ? mode : "hybrid";
  if (modeSafe !== "fulltext") {
    if (!Array.isArray(embedding) || embedding.length !== config.model.dim) {
      res.status(400).json({ error: `查询向量维度不是 ${config.model.dim}` });
      return;
    }
  }
  const result = await db.search(kbId, {
    query: sanitizeQuery(query),
    embedding,
    mode: modeSafe,
    topK,
  });
  res.json({ query: String(query).slice(0, 500), ...result });
}));

app.get("/api/model-info", wrap(async (req, res) => {
  const reportPath = path.join(config.root, "evidence", "model-quantization.json");
  let quantization = null;
  try {
    quantization = JSON.parse(await fs.readFile(reportPath, "utf8"));
  } catch {
    quantization = null;
  }
  let modelBytes = null;
  try {
    const stat = await fs.stat(path.join(config.root, "web", "public", "models", config.model.id, "onnx", "model_int8.onnx"));
    modelBytes = stat.size;
  } catch {
    modelBytes = null;
  }
  res.json({
    model: { ...config.model, int8Bytes: modelBytes ?? config.model.int8Bytes },
    quantization: quantization?.summary ?? null,
    method: quantization?.quantization?.int8 ?? null,
  });
}));

app.use("/models", express.static(path.join(config.root, "web", "public", "models"), {
  maxAge: "7d",
  immutable: true,
}));

app.use(express.static(config.distDir, { index: false, maxAge: "1h" }));

app.get("*", async (req, res, next) => {
  if (req.path.startsWith("/api/")) return next();
  try {
    const html = await fs.readFile(path.join(config.distDir, "index.html"));
    res.setHeader("Cache-Control", "no-cache");
    res.type("html").send(html);
  } catch {
    res.status(404).type("text").send("前端尚未构建：请先运行 npm run build");
  }
});

app.use((error, req, res, next) => { // eslint-disable-line no-unused-vars
  const status = error.status ?? 500;
  if (status >= 500) console.error(error);
  res.status(status).json({ error: error.message ?? "服务器内部错误", code: error.code ?? null });
});

const server = app.listen(config.port, config.host, async () => {
  try {
    await db.connect();
    console.log(`seekdb connected: ${config.seekdb.host}:${config.seekdb.port}/${config.seekdb.database}`);
  } catch (error) {
    console.error("seekdb 连接失败：", error.message);
  }
  console.log(`stepfun mini-kb ready on http://127.0.0.1:${config.port}`);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, async () => {
    console.log(`\n${signal} received, shutting down`);
    server.close();
    await db.close().catch(() => {});
    process.exit(0);
  });
}

export default app;
