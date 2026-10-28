import { AsyncLocalStorage } from "node:async_hooks";
import { randomBytes } from "node:crypto";

import { SeekdbAdminClient, SeekdbClient } from "seekdb";

import config from "./config.mjs";

const traceStore = new AsyncLocalStorage();

let client = null;
let ready = null;

const REGISTRY = "stepfun_kb_registry";

function collectionName(kbId) {
  return `${config.collectionPrefix}${kbId}`;
}

export function assertKbId(kbId) {
  if (!/^[a-z0-9]{6,12}$/.test(String(kbId ?? ""))) {
    const error = new Error("非法知识库 ID");
    error.status = 400;
    throw error;
  }
}

/** Collect every SQL statement executed inside `fn` (used as DB-side evidence). */
export async function withSqlTrace(fn) {
  const store = { statements: [] };
  const result = await traceStore.run(store, fn);
  return { result, statements: store.statements };
}

async function ensureDatabase() {
  const { host, port, user, password, database } = config.seekdb;
  const admin = new SeekdbAdminClient({ host, port, user, password });
  try {
    const databases = await admin.listDatabases(200);
    const exists = databases.some((db) => db.name === database);
    if (!exists) await admin.createDatabase(database);
  } finally {
    await admin.close();
  }
}

async function createRegistry() {
  await client.execute(
    `CREATE TABLE IF NOT EXISTS ${REGISTRY} (
       id VARCHAR(16) PRIMARY KEY,
       name VARCHAR(128) NOT NULL,
       description VARCHAR(512) DEFAULT '',
       analyzer VARCHAR(16) DEFAULT 'ik',
       embed_model VARCHAR(128) DEFAULT '',
       dim INT DEFAULT 512,
       created_at BIGINT NOT NULL,
       updated_at BIGINT NOT NULL,
       imported_chunks INT DEFAULT 0,
       last_refresh BIGINT DEFAULT 0
     )`,
  );
}

function patchSqlTracer(instance) {
  const delegate = instance?._delegate;
  const internal = delegate?._internal;
  const target = internal && typeof internal.execute === "function" ? internal : delegate;
  if (!target || target.__tracePatched || typeof target.execute !== "function") return;
  const original = target.execute.bind(target);
  target.execute = (sql, params) => {
    const store = traceStore.getStore();
    if (store) store.statements.push({ sql, params: params ?? null });
    return original(sql, params);
  };
  target.__tracePatched = true;
}

export async function connect() {
  if (ready) return ready;
  ready = (async () => {
    await ensureDatabase();
    client = new SeekdbClient({
      ...config.seekdb,
      database: config.seekdb.database,
      queryTimeout: config.seekdb.queryTimeout,
    });
    patchSqlTracer(client);
    await createRegistry();
    return client;
  })();
  return ready;
}

export function getClient() {
  if (!client) throw new Error("seekdb 尚未连接");
  return client;
}

async function getCollection(kbId) {
  assertKbId(kbId);
  try {
    return await getClient().getCollection({ name: collectionName(kbId), embeddingFunction: null });
  } catch (error) {
    const notFound = new Error(`知识库 ${kbId} 不存在`);
    notFound.status = 404;
    notFound.cause = error;
    throw notFound;
  }
}

export async function health() {
  const started = performance.now();
  const rows = await getClient().execute("SELECT VERSION() AS version");
  return {
    ok: true,
    version: rows?.[0]?.version ?? "unknown",
    database: config.seekdb.database,
    host: `${config.seekdb.host}:${config.seekdb.port}`,
    latencyMs: Number((performance.now() - started).toFixed(1)),
  };
}

export async function listKbs() {
  const rows = await getClient().execute(
    `SELECT id, name, description, analyzer, dim, created_at, updated_at, imported_chunks, last_refresh
       FROM ${REGISTRY} ORDER BY updated_at DESC`,
  );
  const kbs = [];
  for (const row of rows ?? []) {
    let chunks = 0;
    try {
      chunks = await (await getCollection(row.id)).count();
    } catch {
      chunks = -1;
    }
    kbs.push({
      id: row.id,
      name: row.name,
      description: row.description ?? "",
      analyzer: row.analyzer,
      dim: Number(row.dim),
      createdAt: Number(row.created_at),
      updatedAt: Number(row.updated_at),
      chunks,
      lastRefresh: Number(row.last_refresh),
    });
  }
  return kbs;
}

export async function createKb({ name, description = "" }) {
  const cleanName = String(name ?? "").trim().slice(0, 64);
  if (!cleanName) {
    const error = new Error("知识库名称不能为空");
    error.status = 400;
    throw error;
  }
  const id = randomBytes(4).toString("hex");
  let analyzer = "ik";
  try {
    await getClient().createCollection({
      name: collectionName(id),
      embeddingFunction: null,
      configuration: {
        hnsw: { dimension: config.model.dim, distance: "cosine" },
        fulltextConfig: { analyzer, properties: { ik_mode: "smart" } },
      },
    });
  } catch (error) {
    analyzer = "ngram";
    await getClient().createCollection({
      name: collectionName(id),
      embeddingFunction: null,
      configuration: {
        hnsw: { dimension: config.model.dim, distance: "cosine" },
        fulltextConfig: { analyzer, properties: { ngram_token_size: 2 } },
      },
    });
  }
  const now = Date.now();
  await getClient().execute(
    `INSERT INTO ${REGISTRY} (id, name, description, analyzer, embed_model, dim, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    [id, cleanName, String(description).slice(0, 512), analyzer, config.model.name, config.model.dim, now, now],
  );
  return { id, name: cleanName, description, analyzer, chunks: 0, createdAt: now, updatedAt: now };
}

export async function renameKb(kbId, name) {
  assertKbId(kbId);
  const cleanName = String(name ?? "").trim().slice(0, 64);
  if (!cleanName) {
    const error = new Error("知识库名称不能为空");
    error.status = 400;
    throw error;
  }
  await getClient().execute(`UPDATE ${REGISTRY} SET name = ?, updated_at = ? WHERE id = ?`, [cleanName, Date.now(), kbId]);
}

export async function deleteKb(kbId) {
  assertKbId(kbId);
  await getClient().deleteCollection(collectionName(kbId));
  await getClient().execute(`DELETE FROM ${REGISTRY} WHERE id = ?`, [kbId]);
}

export async function kbStats(kbId) {
  const coll = await getCollection(kbId);
  const rows = await getClient().execute(`SELECT * FROM ${REGISTRY} WHERE id = ?`, [kbId]);
  const row = rows?.[0] ?? {};
  return {
    id: kbId,
    name: row.name ?? kbId,
    description: row.description ?? "",
    analyzer: row.analyzer ?? "ik",
    dim: Number(row.dim ?? config.model.dim),
    chunks: await coll.count(),
    lastRefresh: Number(row.last_refresh ?? 0),
    updatedAt: Number(row.updated_at ?? 0),
    collection: collectionName(kbId),
  };
}

/** Insert (or idempotently replace) embedded chunks, then refresh the indexes. */
export async function upsertChunks(kbId, items) {
  const coll = await getCollection(kbId);
  const started = performance.now();
  for (let i = 0; i < items.length; i += config.limits.addBatchSize) {
    const batch = items.slice(i, i + config.limits.addBatchSize);
    await coll.upsert({
      ids: batch.map((item) => item.id),
      documents: batch.map((item) => item.text),
      embeddings: batch.map((item) => item.embedding),
      metadatas: batch.map((item) => ({
        docId: item.docId,
        docName: item.docName,
        chunk: item.index,
        chunks: item.total,
        lang: item.lang,
        chars: item.chars,
        source: item.source ?? "upload",
      })),
    });
  }
  const writeMs = performance.now() - started;
  const refreshStarted = performance.now();
  await coll.refresh_index();
  const refreshMs = performance.now() - refreshStarted;
  const count = await coll.count();
  await getClient().execute(
    `UPDATE ${REGISTRY} SET updated_at = ?, imported_chunks = imported_chunks + ?, last_refresh = ? WHERE id = ?`,
    [Date.now(), items.length, Date.now(), kbId],
  );
  return {
    inserted: items.length,
    count,
    writeMs: Number(writeMs.toFixed(1)),
    refreshMs: Number(refreshMs.toFixed(1)),
  };
}

function normalizeResults(result, scoreKind) {
  const ids = result.ids?.[0] ?? [];
  const documents = result.documents?.[0] ?? [];
  const metadatas = result.metadatas?.[0] ?? [];
  const distances = result.distances?.[0] ?? [];
  return ids.map((id, index) => {
    const distance = distances[index];
    const metadata = metadatas[index] ?? {};
    return {
      rank: index + 1,
      id,
      text: documents[index] ?? "",
      metadata,
      scoreKind,
      distance: distance ?? null,
      score:
        scoreKind === "cosine"
          ? distance === null || distance === undefined
            ? null
            : Number((1 - Number(distance)).toFixed(4))
          : distance === null || distance === undefined
            ? null
            : Number(Number(distance).toFixed(4)),
    };
  });
}

/**
 * All three modes are executed inside seekdb:
 *  - hybrid : DBMS_HYBRID_SEARCH with RRF over full-text + HNSW knn
 *  - vector : HNSW knn only
 *  - fulltext: full-text query_string only
 */
export async function search(kbId, { query, embedding, mode = "hybrid", topK = 8 }) {
  const coll = await getCollection(kbId);
  const limit = Math.min(Math.max(Number(topK) || 8, 1), 50);
  const safeQuery = String(query ?? "").slice(0, 500);
  const window = Math.max(60, limit * 8);

  const run = async () => {
    if (mode === "vector") {
      const result = await coll.query({
        queryEmbeddings: embedding,
        nResults: limit,
        include: ["documents", "metadatas", "distances"],
      });
      return { results: normalizeResults(result, "cosine"), engine: "HNSW knn (collection.query)" };
    }
    if (mode === "fulltext") {
      const result = await coll.hybridSearch({
        query: { whereDocument: { $contains: safeQuery }, nResults: limit },
        nResults: limit,
        include: ["documents", "metadatas", "distances"],
      });
      return { results: normalizeResults(result, "rrf"), engine: "DBMS_HYBRID_SEARCH (full-text only)" };
    }
    const result = await coll.hybridSearch({
      query: { whereDocument: { $contains: safeQuery }, nResults: window },
      knn: { queryEmbeddings: embedding, nResults: window },
      rank: { rrf: { rankWindowSize: window, rankConstant: 60 } },
      nResults: limit,
      include: ["documents", "metadatas", "distances"],
    });
    return { results: normalizeResults(result, "rrf"), engine: "DBMS_HYBRID_SEARCH (RRF: full-text + HNSW)" };
  };

  const started = performance.now();
  const { result, statements } = await withSqlTrace(run);
  const dbMs = Number((performance.now() - started).toFixed(1));
  return {
    ...result,
    dbMs,
    mode,
    topK: limit,
    sql: statements,
  };
}

/** DDL evidence for the tech note: table definition and index list. */
export async function schemaEvidence(kbId) {
  const coll = await getCollection(kbId);
  const collectionId = coll.collectionId ?? "";
  let table = null;
  if (collectionId) {
    const rows = await getClient().execute(
      `SELECT table_name AS t FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name LIKE ? LIMIT 1`,
      [`%${collectionId}%`],
    );
    table = rows?.[0]?.t ?? null;
  }
  if (!table) {
    const rows = await getClient().execute(
      `SELECT table_name AS t FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name LIKE ? LIMIT 1`,
      [`%${collectionName(kbId)}%`],
    );
    table = rows?.[0]?.t ?? null;
  }
  let createTable = null;
  let indexes = null;
  if (table) {
    const ddl = await getClient().execute(`SHOW CREATE TABLE \`${table}\``);
    createTable = ddl?.[0]?.["Create Table"] ?? ddl?.[0] ?? null;
    indexes = await getClient().execute(`SHOW INDEX FROM \`${table}\``);
  }
  return {
    collection: collectionName(kbId),
    collectionId,
    table,
    createTable,
    indexes,
    dimension: coll.dimension,
    distance: coll.distance,
  };
}

export async function close() {
  if (client) await client.close();
  client = null;
  ready = null;
}
