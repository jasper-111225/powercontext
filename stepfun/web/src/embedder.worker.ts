/// <reference lib="webworker" />
import { env, pipeline, type FeatureExtractionPipeline } from "@huggingface/transformers";

type Backend = "webgpu" | "wasm";

interface InitMessage {
  type: "init";
  requestId: string;
  backend: "auto" | Backend;
  dtype: "int8" | "fp32";
  modelId: string;
  modelPath: string;
  queryPrefix: string;
}

interface EmbedMessage {
  type: "embed";
  requestId: string;
  texts: string[];
  kind: "doc" | "query";
  batchSize?: number;
  batchMode?: "fixed" | "token";
}

type Request = InitMessage | EmbedMessage;

const scope = self as unknown as DedicatedWorkerGlobalScope;

let extractor: FeatureExtractionPipeline | null = null;
let activeBackend: Backend | null = null;
let modelLoadMs = 0;
let warmupMs = 0;
let queryPrefix = "";
let activeDtype: "int8" | "fp32" = "int8";
let adapterInfo: { vendor: string; architecture: string; description: string } | null = null;
let initPromise: Promise<void> | null = null;

const post = (message: unknown) => scope.postMessage(message);

function describeAdapter(adapter: GPUAdapter | null) {
  const info = (adapter as unknown as { info?: { vendor?: string; architecture?: string; description?: string } })?.info;
  if (!info) return null;
  return {
    vendor: info.vendor ?? "unknown",
    architecture: info.architecture ?? "unknown",
    description: info.description ?? "",
  };
}

const SOFTWARE_ADAPTER = /swiftshader|llvmpipe|softpipe|software|microsoft basic/i;

async function selectBackend(preference: "auto" | Backend, notes: string[]): Promise<Backend[]> {
  const order: Backend[] = [];
  const gpu = typeof navigator !== "undefined" && "gpu" in navigator ? navigator.gpu : undefined;
  if (preference !== "wasm" && gpu) {
    try {
      const adapter = await gpu.requestAdapter();
      if (adapter) {
        adapterInfo = describeAdapter(adapter);
        const label = `${adapterInfo.vendor} ${adapterInfo.architecture} ${adapterInfo.description}`;
        const software = SOFTWARE_ADAPTER.test(label);
        // A software WebGPU adapter (SwiftShader / llvmpipe) is usually far
        // slower than multi-threaded WASM, so auto mode skips it. An explicit
        // ?backend=webgpu still uses it, which is how the WebGPU path is tested.
        if (software && preference === "auto") {
          notes.push(`跳过软件渲染 WebGPU（${adapterInfo.architecture || adapterInfo.vendor}），自动模式改用 WASM`);
        } else {
          order.push("webgpu");
        }
      }
    } catch {
      // WebGPU unavailable -> WASM below.
    }
  }
  order.push("wasm");
  return order;
}

async function createExtractor(backend: Backend, modelId: string, dtype: "int8" | "fp32") {
  const device = backend === "webgpu" ? "webgpu" : "wasm";
  return pipeline("feature-extraction", modelId, {
    device,
    dtype,
    local_files_only: true,
    progress_callback: (progress: unknown) => {
      post({ type: "model-progress", progress });
    },
  });
}

async function init(message: InitMessage) {
  const started = performance.now();
  queryPrefix = message.queryPrefix;
  activeDtype = message.dtype === "fp32" ? "fp32" : "int8";
  env.allowRemoteModels = false;
  env.allowLocalModels = true;
  env.localModelPath = message.modelPath.endsWith("/") ? message.modelPath : `${message.modelPath}/`;
  env.useBrowserCache = false;
  // ORT assets are copied to /ort/ by scripts/prepare-assets.mjs so the build
  // never depends on a CDN.
  const ortEnv = env.backends.onnx as unknown as {
    wasm: { numThreads: number; simd: boolean };
  };
  ortEnv.wasm.numThreads = Math.min(4, Math.max(1, navigator.hardwareConcurrency || 2));
  ortEnv.wasm.simd = true;

  const notes: string[] = [];
  const candidates = await selectBackend(message.backend, notes);
  const errors: string[] = [];
  for (const backend of candidates) {
    try {
      post({ type: "backend-trying", backend });
      extractor = await createExtractor(backend, message.modelId, activeDtype);
      activeBackend = backend;
      modelLoadMs = Math.round(performance.now() - started);
      // Warm up: first inference allocates buffers / compiles shaders.
      const warmStarted = performance.now();
      await extractor(["warmup"], { pooling: "cls", normalize: true });
      warmupMs = Math.round(performance.now() - warmStarted);
      post({
        type: "ready",
        requestId: message.requestId,
        backend,
        dtype: activeDtype,
        modelLoadMs,
        warmupMs,
        adapter: adapterInfo,
        crossOriginIsolated: scope.crossOriginIsolated,
        attempts: [...notes, ...errors],
      });
      return;
    } catch (error) {
      errors.push(`${backend}: ${(error as Error).message}`);
      extractor = null;
      activeBackend = null;
    }
  }
  post({
    type: "error",
    requestId: message.requestId,
    message: `模型加载失败：${errors.join(" | ")}`,
    attempts: errors,
  });
}

/**
 * Group texts into batches that minimise padding waste.
 *
 * Fixed-size batches pad every row to the longest text in the batch, which is
 * expensive when chunk lengths vary. Sorting by length and packing a token
 * budget keeps the padded token count much closer to the real one. The output
 * order is restored afterwards, so callers never see the permutation.
 */
function planBatches(texts: string[], maxItems: number, maxTokens: number): number[][] {
  const order = texts.map((text, index) => ({ index, tokens: Math.ceil(text.length * 1.15) + 8 }));
  order.sort((a, b) => a.tokens - b.tokens);
  const batches: number[][] = [];
  let current: number[] = [];
  let longest = 0;
  for (const item of order) {
    const nextLongest = Math.max(longest, item.tokens);
    const fits = current.length < maxItems && (current.length === 0 || nextLongest * (current.length + 1) <= maxTokens);
    if (!fits && current.length) {
      batches.push(current);
      current = [];
      longest = 0;
    }
    current.push(item.index);
    longest = Math.max(longest, item.tokens);
  }
  if (current.length) batches.push(current);
  return batches;
}

async function embed(message: EmbedMessage) {
  if (!extractor || !activeBackend) throw new Error("模型尚未就绪");
  const started = performance.now();
  const isGpu = activeBackend === "webgpu";
  const maxItems = message.batchSize ?? (isGpu ? 8 : 16);
  const maxTokens = isGpu ? 6000 : 5000;
  const batches =
    message.batchMode === "fixed"
      ? Array.from({ length: Math.ceil(message.texts.length / 8) }, (_, i) =>
          Array.from({ length: Math.min(8, message.texts.length - i * 8) }, (_, j) => i * 8 + j),
        )
      : planBatches(message.texts, maxItems, maxTokens);
  const vectors: number[][] = new Array(message.texts.length);
  let chars = 0;
  let done = 0;

  for (const indices of batches) {
    const prepared = indices.map((index) =>
      message.kind === "query" ? `${queryPrefix}${message.texts[index]}` : message.texts[index],
    );
    chars += prepared.reduce((sum, text) => sum + text.length, 0);
    const output = await extractor(prepared, { pooling: "cls", normalize: true });
    const rows = output.tolist() as number[][];
    indices.forEach((index, row) => {
      vectors[index] = rows[row];
    });
    done += indices.length;
    post({
      type: "embed-progress",
      requestId: message.requestId,
      done: Math.min(done, message.texts.length),
      total: message.texts.length,
      elapsedMs: Math.round(performance.now() - started),
    });
  }

  post({
    type: "embed-result",
    requestId: message.requestId,
    vectors,
    elapsedMs: Math.round(performance.now() - started),
    chars,
  });
}

scope.addEventListener("message", (event: MessageEvent<Request>) => {
  const message = event.data;
  if (message.type === "init") {
    initPromise = init(message).catch((error) => {
      post({ type: "error", requestId: message.requestId, message: (error as Error).message });
    });
    return;
  }
  if (message.type === "embed") {
    (async () => {
      try {
        await initPromise;
        await embed(message);
      } catch (error) {
        post({
          type: "embed-error",
          requestId: message.requestId,
          message: (error as Error).message,
        });
      }
    })();
  }
});
