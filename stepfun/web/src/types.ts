export type SearchMode = "hybrid" | "vector" | "fulltext";
export type Backend = "webgpu" | "wasm";

export interface Kb {
  id: string;
  name: string;
  description: string;
  analyzer: string;
  dim: number;
  chunks: number;
  createdAt: number;
  updatedAt: number;
  lastRefresh: number;
}

export interface Health {
  ok: boolean;
  seekdb: { ok: boolean; version: string; database: string; host: string; latencyMs: number };
  model: { id: string; name: string; dim: number; queryPrefix: string; int8Bytes: number; fp32Bytes: number };
  limits: Record<string, number>;
}

export interface ModelInfo {
  model: { id: string; name: string; dim: number; int8Bytes: number; fp32Bytes: number };
  quantization: {
    fp32MB: number;
    int8MB: number;
    int8Compression: number;
  } | null;
  method: { tool: string; weightType: string; perChannel: boolean; activations: string } | null;
}

export interface Sample {
  name: string;
  text: string;
  chars: number;
}

export interface Chunk {
  id: string;
  docId: string;
  docName: string;
  index: number;
  total: number;
  text: string;
  lang: string;
  chars: number;
  source?: string;
}

export interface SearchResult {
  rank: number;
  id: string;
  text: string;
  metadata: Record<string, string | number>;
  scoreKind: "rrf" | "cosine";
  score: number | null;
  distance: number | null;
}

export interface SearchResponse {
  query: string;
  mode: SearchMode;
  engine: string;
  dbMs: number;
  topK: number;
  results: SearchResult[];
  sql: { sql: string; params: unknown }[];
}

export interface IngestProgress {
  phase: "preparing" | "embedding" | "writing" | "refreshing" | "done" | "error";
  total: number;
  done: number;
  percent: number;
  elapsedMs: number;
  chunksPerSec: number;
  message: string;
}

export interface BenchSample {
  label: string;
  value: number;
  unit: string;
}

export interface BenchResult {
  backend: Backend;
  dtype: string;
  rounds: number;
  queries: number;
  generatedAt: string;
  modelLoadMs: number;
  warmupMs: number;
  docEmbed: { chunks: number; totalMs: number; chunksPerSec: number; charsPerSec: number };
  queryEmbedP50Ms: number;
  dbSearchP50Ms: number;
  e2eP50Ms: number;
  e2eP95Ms: number;
  perRound: { queryEmbed: number[]; db: number[]; e2e: number[] };
  environment: {
    userAgent: string;
    hardwareConcurrency: number;
    adapter: { vendor: string; architecture: string; description: string } | null;
    crossOriginIsolated: boolean;
    kb: string;
    chunks: number;
    seekdb: string;
  };
}
