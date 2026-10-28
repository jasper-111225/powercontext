#!/usr/bin/env node
/**
 * Minimal end-to-end data-layer smoke test.
 *
 *   node server/index.mjs &
 *   node scripts/smoke.mjs [baseUrl]
 *
 * Checks: seekdb health -> create KB -> chunk -> upsert 512-d vectors ->
 * refresh -> hybrid search -> schema evidence -> delete KB.
 */
const base = process.argv[2] ?? process.env.APP_URL ?? "http://127.0.0.1:3000";

async function call(path, init) {
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error(`${path}: ${response.status} ${body?.error ?? text}`);
  return body;
}

const checks = [];
const check = (name, ok, detail = "") => {
  checks.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  ${detail}` : ""}`);
};

const health = await call("/api/health");
check("seekdb health", health.ok && health.seekdb.ok, health.seekdb.version);

const { kb } = await call("/api/kbs", {
  method: "POST",
  body: JSON.stringify({ name: `smoke-${Date.now()}`, description: "scripts/smoke.mjs" }),
});
check("create collection", Boolean(kb.id), kb.id);

const text =
  "seekdb 在同一引擎里提供向量检索与全文检索。混合检索用 RRF 在数据库内融合两路结果。\n\n" +
  "浏览器使用 INT8 模型在本地生成 embedding，向量写入 seekdb 后刷新索引即可检索。";
const prepared = await call("/api/ingest/prepare", {
  method: "POST",
  body: JSON.stringify({ kbId: kb.id, files: [{ name: "smoke.md", text }] }),
});
check("chunking", prepared.totalChunks > 0, `${prepared.totalChunks} chunks`);

const vector = Array.from({ length: 512 }, (_, index) => Math.sin((index + 1) / 37));
const items = prepared.chunks.map((chunk) => ({ ...chunk, embedding: vector }));
const commit = await call("/api/ingest/commit", {
  method: "POST",
  body: JSON.stringify({ kbId: kb.id, items }),
});
check("upsert + refresh", commit.ok && commit.count === items.length, `count=${commit.count} write=${commit.writeMs}ms refresh=${commit.refreshMs}ms`);

const search = await call("/api/search", {
  method: "POST",
  body: JSON.stringify({ kbId: kb.id, query: "混合检索", embedding: vector, mode: "hybrid", topK: 3 }),
});
check("hybrid search in seekdb", search.engine.includes("DBMS_HYBRID_SEARCH"), `${search.dbMs}ms, ${search.results.length} results`);
check("SQL trace", Array.isArray(search.sql) && search.sql.length >= 2, `${search.sql.length} statements`);

const schema = await call(`/api/kbs/${kb.id}/schema`);
check("vector + fulltext index", Boolean(schema.createTable?.includes("VECTOR") && schema.createTable?.includes("FULLTEXT")), schema.table ?? "");

await call(`/api/kbs/${kb.id}`, { method: "DELETE" });
check("delete collection", true);

const failed = checks.filter((item) => !item.ok);
console.log(`\n${checks.length - failed.length}/${checks.length} passed`);
process.exit(failed.length ? 1 : 0);
