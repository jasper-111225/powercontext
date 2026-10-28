import type {
  BenchResult,
  Chunk,
  Health,
  Kb,
  ModelInfo,
  Sample,
  SearchMode,
  SearchResponse,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!response.ok) {
    const message =
      (body as { error?: string } | null)?.error ?? `${response.status} ${response.statusText}`;
    throw new Error(message);
  }
  return body as T;
}

export const api = {
  health: () => request<Health>("/api/health"),

  modelInfo: () => request<ModelInfo>("/api/model-info"),

  listKbs: () => request<{ kbs: Kb[] }>("/api/kbs"),

  createKb: (name: string, description = "") =>
    request<{ kb: Kb }>("/api/kbs", {
      method: "POST",
      body: JSON.stringify({ name, description }),
    }),

  renameKb: (id: string, name: string) =>
    request<{ kb: Kb }>(`/api/kbs/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),

  deleteKb: (id: string) => request<{ ok: boolean }>(`/api/kbs/${id}`, { method: "DELETE" }),

  kb: (id: string) => request<{ kb: Kb }>(`/api/kbs/${id}`),

  schema: (id: string) => request<Record<string, unknown>>(`/api/kbs/${id}/schema`),

  samples: () => request<{ samples: Sample[]; totalChars: number }>("/api/samples"),

  prepare: (kbId: string, files: { name: string; text: string; source?: string }[]) =>
    request<{
      kbId: string;
      files: { name: string; chars: number; chunks: number }[];
      chunks: Chunk[];
      totalChunks: number;
      totalChars: number;
    }>("/api/ingest/prepare", {
      method: "POST",
      body: JSON.stringify({ kbId, files }),
    }),

  commit: (
    kbId: string,
    items: (Chunk & { embedding: number[]; source?: string })[],
  ) =>
    request<{ ok: boolean; inserted: number; count: number; writeMs: number; refreshMs: number }>(
      "/api/ingest/commit",
      {
        method: "POST",
        body: JSON.stringify({ kbId, items }),
      },
    ),

  search: (params: {
    kbId: string;
    query: string;
    embedding: number[];
    mode: SearchMode;
    topK: number;
  }) =>
    request<SearchResponse>("/api/search", {
      method: "POST",
      body: JSON.stringify(params),
    }),
};

export type { BenchResult };
