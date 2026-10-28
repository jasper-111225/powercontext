import { useCallback, useEffect, useRef, useState } from "react";

import type { Backend } from "../types";

export interface EmbedderState {
  status: "loading" | "ready" | "error";
  backend: Backend | null;
  dtype: string;
  adapter: { vendor: string; architecture: string; description: string } | null;
  modelLoadMs: number;
  warmupMs: number;
  attempts: string[];
  error: string | null;
  progress: { percent: number; file: string } | null;
  forced: "auto" | Backend;
}

interface Pending {
  resolve: (vectors: number[][]) => void;
  reject: (error: Error) => void;
  onProgress?: (done: number, total: number, elapsedMs: number) => void;
}

const resolveForced = (): "auto" | Backend => {
  const fromUrl = new URLSearchParams(window.location.search).get("backend");
  if (fromUrl === "webgpu" || fromUrl === "wasm" || fromUrl === "auto") return fromUrl;
  const stored = localStorage.getItem("sfkb.backend");
  if (stored === "webgpu" || stored === "wasm" || stored === "auto") return stored;
  return "auto";
};

const initialState = (forced: "auto" | Backend): EmbedderState => ({
  status: "loading",
  backend: null,
  dtype: "int8",
  adapter: null,
  modelLoadMs: 0,
  warmupMs: 0,
  attempts: [],
  error: null,
  progress: null,
  forced,
});

export function useEmbedder(modelId: string, modelPath: string, queryPrefix: string, dtype: "int8" | "fp32") {
  const workerRef = useRef<Worker | null>(null);
  const pendingRef = useRef(new Map<string, Pending>());
  const counterRef = useRef(0);
  const [state, setState] = useState<EmbedderState>(() => initialState(resolveForced()));

  const boot = useCallback(
    (forced: "auto" | Backend, persist = true) => {
      workerRef.current?.terminate();
      pendingRef.current.forEach((pending) => pending.reject(new Error("embedder restarted")));
      pendingRef.current.clear();
      setState(initialState(forced));
      if (persist) localStorage.setItem("sfkb.backend", forced);

      const worker = new Worker(new URL("./embedder.worker.ts", import.meta.url), { type: "module" });
      workerRef.current = worker;

      worker.addEventListener("message", (event: MessageEvent) => {
        const message = event.data as {
          type: string;
          requestId?: string;
          backend?: Backend;
          dtype?: string;
          adapter?: EmbedderState["adapter"];
          modelLoadMs?: number;
          warmupMs?: number;
          attempts?: string[];
          message?: string;
          progress?: unknown;
          done?: number;
          total?: number;
          elapsedMs?: number;
          vectors?: number[][];
        };

        if (message.type === "ready") {
          setState((prev) => ({
            ...prev,
            status: "ready",
            backend: message.backend ?? null,
            dtype: message.dtype ?? "int8",
            adapter: message.adapter ?? null,
            modelLoadMs: message.modelLoadMs ?? 0,
            warmupMs: message.warmupMs ?? 0,
            attempts: message.attempts ?? [],
            progress: null,
          }));
          return;
        }
        if (message.type === "error") {
          setState((prev) => ({
            ...prev,
            status: "error",
            error: message.message ?? "模型加载失败",
            attempts: message.attempts ?? [],
          }));
          return;
        }
        if (message.type === "model-progress") {
          const raw = message.progress as { progress?: number; file?: string; status?: string } | undefined;
          if (raw && typeof raw.progress === "number") {
            setState((prev) => ({
              ...prev,
              progress: { percent: Math.round(raw.progress), file: raw.file ?? "" },
            }));
          }
          return;
        }
        if (message.type === "embed-progress" && message.requestId) {
          pendingRef.current
            .get(message.requestId)
            ?.onProgress?.(message.done ?? 0, message.total ?? 0, message.elapsedMs ?? 0);
          return;
        }
        if (message.type === "embed-result" && message.requestId) {
          const pending = pendingRef.current.get(message.requestId);
          pendingRef.current.delete(message.requestId);
          pending?.resolve(message.vectors ?? []);
          return;
        }
        if (message.type === "embed-error" && message.requestId) {
          const pending = pendingRef.current.get(message.requestId);
          pendingRef.current.delete(message.requestId);
          pending?.reject(new Error(message.message ?? "向量化失败"));
        }
      });

      worker.addEventListener("error", (event) => {
        setState((prev) => ({ ...prev, status: "error", error: event.message || "Worker 启动失败" }));
      });

      const requestId = `init-${Date.now()}`;
      worker.postMessage({
        type: "init",
        requestId,
        backend: forced,
        dtype,
        modelId,
        modelPath,
        queryPrefix,
      });
    },
    [dtype, modelId, modelPath, queryPrefix],
  );

  const embed = useCallback(
    (
      texts: string[],
      kind: "doc" | "query",
      onProgress?: (done: number, total: number, elapsedMs: number) => void,
      batchMode?: "fixed" | "token",
    ) =>
      new Promise<number[][]>((resolve, reject) => {
        const worker = workerRef.current;
        if (!worker) {
          reject(new Error("embedder 尚未启动"));
          return;
        }
        counterRef.current += 1;
        const requestId = `embed-${counterRef.current}`;
        pendingRef.current.set(requestId, { resolve, reject, onProgress });
        worker.postMessage({ type: "embed", requestId, texts, kind, batchMode });
      }),
    [],
  );

  useEffect(() => {
    boot(resolveForced(), false);
    return () => {
      workerRef.current?.terminate();
      workerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const restart = useCallback((forced: "auto" | Backend) => boot(forced, true), [boot]);

  return { state, embed, restart };
}
