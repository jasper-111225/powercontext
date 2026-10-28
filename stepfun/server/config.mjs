import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const num = (value, fallback) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
};

export const config = {
  root,
  port: num(process.env.PORT, 3000),
  host: process.env.HOST || "0.0.0.0",
  seekdb: {
    host: process.env.SEEKDB_HOST || "127.0.0.1",
    port: num(process.env.SEEKDB_PORT, 2881),
    user: process.env.SEEKDB_USER || "root",
    password: process.env.SEEKDB_PASSWORD || "",
    database: process.env.SEEKDB_DATABASE || "stepfun_kb",
    queryTimeout: num(process.env.SEEKDB_QUERY_TIMEOUT, 30000),
  },
  collectionPrefix: "kb_",
  model: {
    id: process.env.EMBED_MODEL || "bge-small-zh-v1.5",
    name: "BAAI/bge-small-zh-v1.5 (ONNX, INT8)",
    dim: 512,
    queryPrefix: "",
    int8Bytes: 24020718,
    fp32Bytes: 94851877,
  },
  limits: {
    maxFiles: 20,
    maxFileBytes: 2 * 1024 * 1024,
    maxChunksPerDoc: 400,
    maxChunksPerImport: 2000,
    addBatchSize: 100,
  },
  corpusDir: path.join(root, "data", "corpus"),
  distDir: path.join(root, "dist"),
};

export default config;
