import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api";
import { useEmbedder } from "./useEmbedder";
import type {
  Backend,
  BenchResult,
  Health,
  Kb,
  ModelInfo,
  Sample,
  SearchMode,
  SearchResponse,
} from "./types";

const MODEL_ID = "bge-small-zh-v1.5";
const MODEL_PATH = "/models/";
const QUERY_PREFIX = ""; // 见技术说明：自然语言问句的消融显示不加指令前缀更优
const DTYPE: "int8" | "fp32" =
  new URLSearchParams(window.location.search).get("dtype") === "fp32" ? "fp32" : "int8";

const QUERY_SUGGESTIONS = [
  "seekdb 怎么同时做向量检索和全文检索？",
  "INT8 量化对检索质量有什么影响？",
  "浏览器里如何在没有 GPU 时降级到 WASM？",
  "RRF 是怎么融合两路检索结果的？",
];

const BENCH_QUERIES = [
  "seekdb 的混合检索是怎么实现的？",
  "端侧 embedding 模型的量化方法",
  "WebGPU 不可用时如何降级",
  "HNSW 索引与向量近邻搜索",
  "全文检索的分词器与倒排索引",
];

const BENCH_DOCS = [
  "seekdb 是 OceanBase 推出的 AI 原生数据库，在同一个引擎里提供向量索引、全文索引和 JSON 能力。混合检索接口把向量近邻结果与全文匹配结果在数据库内部用 RRF 融合，客户端只发起一次查询。",
  "端侧推理把模型放在浏览器里运行，文档不会离开用户设备。Transformers.js 负责分词与后处理，ONNX Runtime Web 负责执行计算图，WebGPU 可用时优先使用 GPU，否则回退到 WASM。",
  "INT8 动态量化只量化权重，激活值在推理时动态计算缩放因子。对 bge-small-zh-v1.5 而言，权重从 FP32 的 94.9MB 降到 24.0MB，压缩比约 3.95 倍，句向量余弦相似度保持在 0.99 以上。",
  "HNSW 是一种分层可导航小世界图索引，查询时从稀疏的顶层开始贪心搜索，再逐层下降到稠密底层，用较小的访存代价换取高召回率。seekdb 支持 cosine、l2 与 inner product 三种距离。",
  "全文检索依赖分词器和倒排索引。中文可以用 IK 分词器，把句子切成词项后建立倒排表；查询时对词项求交并计算相关性分数。混合检索把全文分数与向量距离统一到排名空间。",
  "RRF 即倒数排名融合，对每一路检索结果按名次计算 1/(k+rank) 并求和。它不需要两路分数同分布，因此适合把 BM25 这类词法分数与余弦距离融合，k 通常取 60。",
  "索引刷新是写入可见性的关键。新写入的向量先进入增量结构，调用 refresh_index 后才会合并进可检索的索引；应用在每次导入结束后显式刷新，保证新增内容立即可查。",
  "知识库的增量写入以文档内容哈希和分块序号作为主键，重复导入同一文件会覆盖旧分块而不是产生重复数据。切换集合时前端只保留当前知识库的统计与检索结果，避免会话串库。",
];

type IngestState = {
  phase: "preparing" | "embedding" | "writing" | "done" | "error";
  total: number;
  done: number;
  percent: number;
  elapsedMs: number;
  chunksPerSec: number;
  message: string;
};

type LastIngest = {
  chunks: number;
  docs: number;
  prepareMs: number;
  embedMs: number;
  writeMs: number;
  refreshMs: number;
  totalMs: number;
  backend: Backend | null;
};

type SearchState = {
  loading: boolean;
  response: SearchResponse | null;
  timings: { embed: number; db: number; e2e: number } | null;
  error?: string;
};

const formatBytes = (bytes: number | null | undefined) => {
  if (!bytes || bytes <= 0) return "—";
  return `${(bytes / 1e6).toFixed(1)} MB`;
};

const formatMs = (ms: number | null | undefined) => {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${ms.toFixed(0)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
};

const median = (values: number[]) => {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

const p95 = (values: number[]) => {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1)];
};

function highlight(text: string, query: string) {
  const terms = Array.from(
    new Set(
      query
        .split(/[\s，。？！、,.?!]+/)
        .map((t) => t.trim())
        .filter((t) => t.length >= 2),
    ),
  ).slice(0, 6);
  if (!terms.length) return text;
  const lookup = new Set(terms.map((t) => t.toLowerCase()));
  const pattern = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  const parts = text.split(pattern);
  return parts.map((part, index) =>
    lookup.has(part.toLowerCase()) ? <mark key={index}>{part}</mark> : <span key={index}>{part}</span>,
  );
}

function BackendBadge({ backend, loading }: { backend: Backend | null; loading: boolean }) {
  if (loading) {
    return (
      <span className="badge badge--loading">
        <span className="dot dot--pulse" /> 加载模型
      </span>
    );
  }
  if (backend === "webgpu") {
    return (
      <span className="badge badge--gpu">
        <span className="dot" /> WebGPU
      </span>
    );
  }
  return (
    <span className="badge badge--wasm">
      <span className="dot" /> WASM 降级
    </span>
  );
}

function ScoreChip({ score, kind }: { score: number | null; kind: "rrf" | "cosine" }) {
  if (score === null) return null;
  return (
    <span className="score" title={kind === "rrf" ? "seekdb RRF 融合分数（越高越好）" : "余弦相似度（越高越好）"}>
      {kind === "rrf" ? "RRF" : "cos"} {score.toFixed(4)}
    </span>
  );
}

function Pipeline({ active, done }: { active: string; done: string[] }) {
  const steps = [
    { key: "chunk", label: "分块" },
    { key: "embed", label: "本地向量化" },
    { key: "write", label: "写入 seekdb" },
    { key: "refresh", label: "刷新索引" },
    { key: "search", label: "DB 内混合检索" },
  ];
  return (
    <div className="pipeline">
      {steps.map((step, index) => (
        <div
          key={step.key}
          className={`pipeline__step ${active === step.key ? "is-active" : ""} ${done.includes(step.key) ? "is-done" : ""}`}
        >
          <span className="pipeline__index">{done.includes(step.key) ? "✓" : index + 1}</span>
          <span>{step.label}</span>
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [modelInfo, setModelInfo] = useState<ModelInfo | null>(null);
  const [kbs, setKbs] = useState<Kb[]>([]);
  const [kbId, setKbId] = useState<string>("");
  const [samples, setSamples] = useState<Sample[]>([]);
  const [ingest, setIngest] = useState<IngestState | null>(null);
  const [lastIngest, setLastIngest] = useState<LastIngest | null>(null);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [topK, setTopK] = useState(8);
  const [search, setSearch] = useState<SearchState>({ loading: false, response: null, timings: null });
  const [bench, setBench] = useState<BenchResult | null>(null);
  const [benchRunning, setBenchRunning] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newKbName, setNewKbName] = useState("");
  const [dragOver, setDragOver] = useState(false);

  const sessionId = useMemo(() => {
    const existing = localStorage.getItem("sfkb.session");
    if (existing) return existing;
    const created = Math.random().toString(36).slice(2, 8);
    localStorage.setItem("sfkb.session", created);
    return created;
  }, []);

  const { state: embedder, embed, restart } = useEmbedder(MODEL_ID, MODEL_PATH, QUERY_PREFIX, DTYPE);

  const kbIdRef = useRef(kbId);
  const queryRef = useRef(query);
  const modeRef = useRef(mode);
  const topKRef = useRef(topK);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    kbIdRef.current = kbId;
  }, [kbId]);
  useEffect(() => {
    queryRef.current = query;
  }, [query]);
  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);
  useEffect(() => {
    topKRef.current = topK;
  }, [topK]);

  const currentKb = kbs.find((kb) => kb.id === kbId) ?? null;

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 3200);
    return () => clearTimeout(timer);
  }, [toast]);

  const refreshKbs = useCallback(async (selectId?: string) => {
    const { kbs: nextKbs } = await api.listKbs();
    setKbs(nextKbs);
    const wanted = selectId ?? kbIdRef.current;
    const nextId = nextKbs.find((kb) => kb.id === wanted)?.id ?? nextKbs[0]?.id ?? "";
    if (nextId && nextId !== kbIdRef.current) {
      kbIdRef.current = nextId;
      setKbId(nextId);
      localStorage.setItem("sfkb.kb", nextId);
    }
    return nextKbs;
  }, []);

  const createKb = useCallback(
    async (name: string) => {
      const { kb } = await api.createKb(name);
      kbIdRef.current = kb.id;
      setKbId(kb.id);
      localStorage.setItem("sfkb.kb", kb.id);
      await refreshKbs(kb.id);
      setToast(`已创建知识库「${kb.name}」`);
      return kb;
    },
    [refreshKbs],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [healthInfo, model, list, sampleList] = await Promise.all([
          api.health(),
          api.modelInfo(),
          api.listKbs(),
          api.samples().catch(() => ({ samples: [], totalChars: 0 })),
        ]);
        if (cancelled) return;
        setHealth(healthInfo);
        setModelInfo(model);
        setSamples(sampleList.samples);
        setKbs(list.kbs);
        const stored = localStorage.getItem("sfkb.kb") ?? "";
        const initial = list.kbs.find((kb) => kb.id === stored)?.id ?? list.kbs[0]?.id ?? "";
        if (initial) {
          kbIdRef.current = initial;
          setKbId(initial);
          localStorage.setItem("sfkb.kb", initial);
        }
      } catch (err) {
        if (!cancelled) setError(`无法连接本地服务：${(err as Error).message}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const selectKb = useCallback((id: string) => {
    kbIdRef.current = id;
    setKbId(id);
    localStorage.setItem("sfkb.kb", id);
    setSearch({ loading: false, response: null, timings: null });
  }, []);

  const ingestFiles = useCallback(
    async (files: { name: string; text: string; source?: string }[]) => {
      setError(null);
      try {
        let id = kbIdRef.current;
        if (!id) {
          const kb = await createKb("我的知识库");
          id = kb.id;
        }
        const started = performance.now();
        setIngest({
          phase: "preparing",
          total: 0,
          done: 0,
          percent: 0,
          elapsedMs: 0,
          chunksPerSec: 0,
          message: "切分文档…",
        });
        const prepared = await api.prepare(id, files);
        const prepareMs = performance.now() - started;
        if (!prepared.totalChunks) throw new Error("没有可导入的文本");

        const embedStarted = performance.now();
        setIngest({
          phase: "embedding",
          total: prepared.totalChunks,
          done: 0,
          percent: 0,
          elapsedMs: 0,
          chunksPerSec: 0,
          message: `本地向量化 ${prepared.totalChunks} 块（${embedder.backend ?? "初始化"}）`,
        });
        const vectors = await embed(prepared.chunks.map((chunk) => chunk.text), "doc", (done, total, elapsedMs) => {
          setIngest({
            phase: "embedding",
            total,
            done,
            percent: Math.round((done / Math.max(1, total)) * 100),
            elapsedMs,
            chunksPerSec: done / Math.max(0.001, elapsedMs / 1000),
            message: `本地向量化 ${done}/${total} 块`,
          });
        });
        const embedMs = performance.now() - embedStarted;
        if (vectors.length !== prepared.chunks.length) throw new Error("向量数量与分块数量不一致");

        setIngest({
          phase: "writing",
          total: prepared.totalChunks,
          done: prepared.totalChunks,
          percent: 100,
          elapsedMs: performance.now() - started,
          chunksPerSec: prepared.totalChunks / Math.max(0.001, embedMs / 1000),
          message: "写入 seekdb 并刷新索引…",
        });
        const commit = await api.commit(
          id,
          prepared.chunks.map((chunk, index) => ({ ...chunk, embedding: vectors[index] })),
        );
        const totalMs = performance.now() - started;
        setIngest({
          phase: "done",
          total: prepared.totalChunks,
          done: prepared.totalChunks,
          percent: 100,
          elapsedMs: totalMs,
          chunksPerSec: prepared.totalChunks / Math.max(0.001, embedMs / 1000),
          message: `已写入 ${commit.inserted} 块，索引已刷新`,
        });
        setLastIngest({
          chunks: commit.inserted,
          docs: prepared.files.length,
          prepareMs,
          embedMs,
          writeMs: commit.writeMs,
          refreshMs: commit.refreshMs,
          totalMs,
          backend: embedder.backend,
        });
        await refreshKbs(id);
        setToast(`导入完成：${prepared.files.length} 篇 / ${commit.inserted} 块`);
        return commit;
      } catch (err) {
        setIngest((prev) => ({
          phase: "error",
          total: prev?.total ?? 0,
          done: prev?.done ?? 0,
          percent: prev?.percent ?? 0,
          elapsedMs: prev?.elapsedMs ?? 0,
          chunksPerSec: 0,
          message: (err as Error).message,
        }));
        setError((err as Error).message);
        throw err;
      }
    },
    [createKb, embed, embedder.backend, refreshKbs],
  );

  const loadSamples = useCallback(async () => {
    if (!samples.length) throw new Error("示例语料为空");
    return ingestFiles(samples.map((sample) => ({ name: sample.name, text: sample.text, source: "sample" })));
  }, [ingestFiles, samples]);

  const handleFileList = useCallback(
    async (list: FileList | File[]) => {
      const files = Array.from(list).slice(0, 20);
      const loaded = await Promise.all(
        files.map(async (file) => ({ name: file.name, text: await file.text() })),
      );
      await ingestFiles(loaded);
    },
    [ingestFiles],
  );

  const doSearch = useCallback(
    async (rawQuery?: string, opts?: { mode?: SearchMode; topK?: number }) => {
      const text = (rawQuery ?? queryRef.current).trim();
      if (!text) return null;
      if (!kbIdRef.current) {
        setError("请先创建知识库并导入语料");
        return null;
      }
      setError(null);
      setSearch({ loading: true, response: null, timings: null });
      const started = performance.now();
      try {
        const [vector] = await embed([text], "query");
        const afterEmbed = performance.now();
        const response = await api.search({
          kbId: kbIdRef.current,
          query: text,
          embedding: vector,
          mode: opts?.mode ?? modeRef.current,
          topK: opts?.topK ?? topKRef.current,
        });
        const finished = performance.now();
        setSearch({
          loading: false,
          response,
          timings: { embed: afterEmbed - started, db: finished - afterEmbed, e2e: finished - started },
        });
        setQuery(text);
        return response;
      } catch (err) {
        setSearch({ loading: false, response: null, timings: null, error: (err as Error).message });
        setError((err as Error).message);
        return null;
      }
    },
    [embed],
  );

  const runBench = useCallback(async (rounds = 3) => {
    if (benchRunning) return null;
    if (!kbIdRef.current) throw new Error("请先导入语料再运行基准");
    setBenchRunning(true);
    setError(null);
    try {
      const docStarted = performance.now();
      await embed(BENCH_DOCS, "doc");
      const docMs = performance.now() - docStarted;

      const queryEmbed: number[] = [];
      const db: number[] = [];
      const e2e: number[] = [];
      for (let round = 0; round < rounds; round += 1) {
        for (const text of BENCH_QUERIES) {
          const t0 = performance.now();
          const [vector] = await embed([text], "query");
          const t1 = performance.now();
          await api.search({
            kbId: kbIdRef.current,
            query: text,
            embedding: vector,
            mode: "hybrid",
            topK: topKRef.current,
          });
          const t2 = performance.now();
          queryEmbed.push(t1 - t0);
          db.push(t2 - t1);
          e2e.push(t2 - t0);
        }
      }

      const result: BenchResult = {
        backend: embedder.backend ?? "wasm",
        dtype: embedder.dtype,
        rounds,
        queries: BENCH_QUERIES.length,
        generatedAt: new Date().toISOString(),
        modelLoadMs: embedder.modelLoadMs,
        warmupMs: embedder.warmupMs,
        docEmbed: {
          chunks: BENCH_DOCS.length,
          totalMs: Number(docMs.toFixed(1)),
          chunksPerSec: Number((BENCH_DOCS.length / (docMs / 1000)).toFixed(2)),
          charsPerSec: Number(
            (BENCH_DOCS.reduce((sum, doc) => sum + doc.length, 0) / (docMs / 1000)).toFixed(0),
          ),
        },
        queryEmbedP50Ms: Number(median(queryEmbed).toFixed(1)),
        dbSearchP50Ms: Number(median(db).toFixed(1)),
        e2eP50Ms: Number(median(e2e).toFixed(1)),
        e2eP95Ms: Number(p95(e2e).toFixed(1)),
        perRound: { queryEmbed, db, e2e },
        environment: {
          userAgent: navigator.userAgent,
          hardwareConcurrency: navigator.hardwareConcurrency,
          adapter: embedder.adapter,
          crossOriginIsolated: window.crossOriginIsolated,
          kb: currentKb?.name ?? "",
          chunks: currentKb?.chunks ?? 0,
          seekdb: health?.seekdb.version ?? "",
        },
      };
      setBench(result);
      (window as unknown as { __sfkbBench?: BenchResult }).__sfkbBench = result;
      setToast(`基准完成：端到端 P50 ${formatMs(result.e2eP50Ms)}`);
      return result;
    } finally {
      setBenchRunning(false);
    }
  }, [benchRunning, currentKb, embed, embedder, health]);

  const exportBench = useCallback(() => {
    if (!bench) return;
    const blob = new Blob([JSON.stringify(bench, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `stepfun-bench-${bench.backend}-${Date.now()}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }, [bench]);

  // Small automation surface used by scripts/bench-browser.mjs and e2e tests.
  useEffect(() => {
    (window as unknown as { __sfkb: unknown }).__sfkb = {
      getState: () => ({
        embedder,
        kbId: kbIdRef.current,
        kbs,
        chunks: currentKb?.chunks ?? 0,
        samples: samples.length,
        lastIngest,
        bench,
        lastSearch: search.response,
        ready: embedder.status === "ready",
      }),
      createKb,
      selectKb,
      loadSamples,
      ingestText: (name: string, text: string) => ingestFiles([{ name, text }]),
      ingestFiles,
      search: (text?: string, opts?: { mode?: SearchMode; topK?: number }) => doSearch(text, opts),
      runBench,
      embedDocs: (texts: string[], batchMode?: "fixed" | "token") => embed(texts, "doc", undefined, batchMode),
      restartEmbedder: restart,
      setMode,
    };
  }, [bench, createKb, currentKb, doSearch, embedder, health, ingestFiles, kbs, lastIngest, loadSamples, restart, samples.length, search.response]);

  const pipeline = useMemo(() => {
    if (search.loading) return { active: "search", done: ["chunk", "embed", "write", "refresh"] };
    if (ingest) {
      if (ingest.phase === "preparing") return { active: "chunk", done: [] as string[] };
      if (ingest.phase === "embedding") return { active: "embed", done: ["chunk"] };
      if (ingest.phase === "writing") return { active: "write", done: ["chunk", "embed"] };
      if (ingest.phase === "done") return { active: "search", done: ["chunk", "embed", "write", "refresh"] };
    }
    return { active: "", done: [] as string[] };
  }, [ingest, search.loading]);

  const runtimeReady = embedder.status === "ready";
  const fallback = runtimeReady && embedder.backend === "wasm";
  const totalSampleChars = samples.reduce((sum, sample) => sum + sample.chars, 0);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand__mark" aria-hidden>
            <span />
            <span />
            <span />
          </div>
          <div>
            <h1>seekdb · 端侧知识库</h1>
            <p>浏览器本地 embedding × seekdb 向量 + 全文混合检索</p>
          </div>
        </div>
        <div className="topbar__right">
          <BackendBadge backend={embedder.backend} loading={embedder.status === "loading"} />
          <span className="chip chip--ghost">会话 {sessionId}</span>
          {health ? <span className="chip chip--ghost">seekdb {health.seekdb.version.split("seekdb-")[1] ?? health.seekdb.version}</span> : null}
        </div>
      </header>

      {error ? (
        <div className="banner banner--error" role="alert">
          <strong>出错了：</strong> {error}
          <button className="link" onClick={() => setError(null)}>关闭</button>
        </div>
      ) : null}

      {fallback ? (
        <div className="banner banner--warn">
          <span className="badge badge--wasm"><span className="dot" /> WASM</span>
          当前环境未使用硬件 WebGPU（没有可用适配器，或适配器为软件渲染），已自动选择 WASM 后端；向量化与检索流程完全一致，仅推理速度不同。
          <span className="banner__meta">WASM 线程 {Math.min(4, navigator.hardwareConcurrency || 2)} · COOP/COEP {window.crossOriginIsolated ? "已开启" : "未开启"}</span>
        </div>
      ) : null}

      <main className="grid">
        <section className="col col--left">
          <div className="card">
            <div className="card__head">
              <h2>知识库</h2>
              <button className="button button--ghost" onClick={() => setShowCreate((prev) => !prev)}>
                {showCreate ? "取消" : "新建"}
              </button>
            </div>
            {showCreate ? (
              <form
                className="create-form"
                onSubmit={async (event) => {
                  event.preventDefault();
                  if (!newKbName.trim()) return;
                  try {
                    await createKb(newKbName.trim());
                    setNewKbName("");
                    setShowCreate(false);
                  } catch (err) {
                    setError((err as Error).message);
                  }
                }}
              >
                <input
                  autoFocus
                  value={newKbName}
                  onChange={(event) => setNewKbName(event.target.value)}
                  placeholder="知识库名称，如：论文笔记"
                  maxLength={64}
                />
                <button className="button" type="submit">创建</button>
              </form>
            ) : null}
            {kbs.length ? (
              <div className="kb-list">
                {kbs.map((kb) => (
                  <button
                    key={kb.id}
                    className={`kb ${kb.id === kbId ? "is-active" : ""}`}
                    onClick={() => selectKb(kb.id)}
                  >
                    <span className="kb__name">{kb.name}</span>
                    <span className="kb__meta">
                      {kb.chunks} 块 · {kb.analyzer}
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="muted">还没有知识库。导入第一篇文档时会自动创建。</p>
            )}
            {currentKb ? (
              <div className="kb-detail">
                <div>
                  <span className="muted">集合</span>
                  <code>{currentKb.id}</code>
                </div>
                <div>
                  <span className="muted">分块</span>
                  <strong>{currentKb.chunks}</strong>
                </div>
                <div>
                  <span className="muted">最后刷新</span>
                  <strong>{currentKb.lastRefresh ? new Date(currentKb.lastRefresh).toLocaleTimeString() : "—"}</strong>
                </div>
                <button
                  className="link link--danger"
                  onClick={async () => {
                    if (!window.confirm(`删除知识库「${currentKb.name}」及其中所有分块？`)) return;
                    await api.deleteKb(currentKb.id);
                    await refreshKbs();
                    setSearch({ loading: false, response: null, timings: null });
                    setToast("知识库已删除");
                  }}
                >
                  删除当前知识库
                </button>
              </div>
            ) : null}
          </div>

          <div
            className={`card dropzone ${dragOver ? "is-over" : ""}`}
            onDragOver={(event) => {
              event.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={async (event) => {
              event.preventDefault();
              setDragOver(false);
              if (event.dataTransfer.files?.length) {
                await handleFileList(event.dataTransfer.files).catch((err) => setError((err as Error).message));
              }
            }}
          >
            <div className="card__head">
              <h2>导入语料</h2>
              <span className="muted">txt / md</span>
            </div>
            <p className="muted">
              文档在本机切块后，由浏览器里的 INT8 模型逐块编码，向量与原文一起写入 seekdb。示例语料共 {samples.length} 篇、
              {(totalSampleChars / 1000).toFixed(0)}k 字符。
            </p>
            <div className="actions">
              <button
                className="button"
                disabled={!runtimeReady || ingest?.phase === "embedding" || ingest?.phase === "writing"}
                onClick={() => fileInputRef.current?.click()}
              >
                选择文件
              </button>
              <button
                className="button button--ghost"
                disabled={!runtimeReady || !samples.length || ingest?.phase === "embedding" || ingest?.phase === "writing"}
                onClick={() => loadSamples().catch((err) => setError((err as Error).message))}
              >
                导入示例语料
              </button>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".txt,.md,.markdown,text/plain,text/markdown"
                hidden
                onChange={(event) => {
                  if (event.target.files?.length) {
                    handleFileList(event.target.files).catch((err) => setError((err as Error).message));
                    event.target.value = "";
                  }
                }}
              />
            </div>

            <Pipeline active={pipeline.active} done={pipeline.done} />

            {ingest ? (
              <div className={`progress ${ingest.phase === "error" ? "progress--error" : ""}`}>
                <div className="progress__bar">
                  <span style={{ width: `${ingest.percent}%` }} />
                </div>
                <div className="progress__meta">
                  <span>{ingest.message}</span>
                  <span>
                    {ingest.total ? `${ingest.done}/${ingest.total} 块` : ""}
                    {ingest.chunksPerSec ? ` · ${ingest.chunksPerSec.toFixed(1)} 块/秒` : ""}
                    {ingest.elapsedMs ? ` · ${formatMs(ingest.elapsedMs)}` : ""}
                  </span>
                </div>
              </div>
            ) : null}

            {lastIngest ? (
              <div className="ingest-summary">
                <div><span>分块</span><strong>{formatMs(lastIngest.prepareMs)}</strong></div>
                <div><span>本地向量化</span><strong>{formatMs(lastIngest.embedMs)}</strong></div>
                <div><span>写入 seekdb</span><strong>{formatMs(lastIngest.writeMs)}</strong></div>
                <div><span>刷新索引</span><strong>{formatMs(lastIngest.refreshMs)}</strong></div>
                <p className="muted">
                  {lastIngest.docs} 篇 / {lastIngest.chunks} 块，端到端 {formatMs(lastIngest.totalMs)} · {lastIngest.backend ?? "—"}
                </p>
              </div>
            ) : null}
          </div>
        </section>

        <section className="col col--center">
          <div className="card search-card">
            <div className="search-row">
              <input
                className="search-input"
                value={query}
                placeholder="问点什么，例如：seekdb 如何做混合检索？"
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") doSearch().catch((err) => setError((err as Error).message));
                }}
              />
              <button
                className="button button--primary"
                disabled={!runtimeReady || search.loading || !kbId}
                onClick={() => doSearch().catch((err) => setError((err as Error).message))}
              >
                {search.loading ? "检索中…" : "检索"}
              </button>
            </div>
            <div className="search-options">
              <div className="segmented">
                {(["hybrid", "vector", "fulltext"] as SearchMode[]).map((item) => (
                  <button
                    key={item}
                    className={mode === item ? "is-active" : ""}
                    onClick={() => setMode(item)}
                  >
                    {item === "hybrid" ? "混合" : item === "vector" ? "向量" : "全文"}
                  </button>
                ))}
              </div>
              <label className="topk">
                TopK
                <input
                  type="number"
                  min={1}
                  max={30}
                  value={topK}
                  onChange={(event) => setTopK(Math.min(30, Math.max(1, Number(event.target.value) || 8)))}
                />
              </label>
              <span className="muted search-engine">
                {search.response?.engine ?? "seekdb DBMS_HYBRID_SEARCH · RRF"}
              </span>
            </div>
            <div className="suggestions">
              {QUERY_SUGGESTIONS.map((text) => (
                <button key={text} className="chip" onClick={() => doSearch(text).catch((err) => setError((err as Error).message))}>
                  {text}
                </button>
              ))}
            </div>
          </div>

          {search.timings ? (
            <div className="timing-row">
              <span>查询向量化 <strong>{formatMs(search.timings.embed)}</strong></span>
              <span>seekdb 检索 <strong>{formatMs(search.timings.db)}</strong></span>
              <span className="timing-row__total">端到端 <strong>{formatMs(search.timings.e2e)}</strong></span>
              <span className="muted">{embedder.backend === "webgpu" ? "WebGPU" : "WASM"} · {embedder.dtype}</span>
            </div>
          ) : null}

          <div className="results">
            {search.loading ? (
              <div className="card skeleton">
                <div className="skeleton__line" />
                <div className="skeleton__line skeleton__line--short" />
                <div className="skeleton__line" />
              </div>
            ) : null}

            {!search.loading && search.response?.results?.length === 0 ? (
              <div className="card empty">
                <p>没有命中结果。</p>
                <p className="muted">全文检索按 IK 分词匹配；若问题与语料差异较大，可切换到「向量」或「混合」。</p>
              </div>
            ) : null}

            {!search.loading && search.response?.results?.map((result) => (
              <article className="card result" key={result.id}>
                <div className="result__head">
                  <span className="rank">{result.rank}</span>
                  <div className="result__title">
                    <strong>{String(result.metadata.docName ?? "未命名文档")}</strong>
                    <span className="muted">
                      第 {Number(result.metadata.chunk ?? 0) + 1}/{Number(result.metadata.chunks ?? 1)} 块 · {String(result.metadata.lang ?? "")} · {Number(result.metadata.chars ?? 0)} 字符
                    </span>
                  </div>
                  <ScoreChip score={result.score} kind={result.scoreKind} />
                </div>
                <p className="result__text">{highlight(result.text, search.response?.query ?? "")}</p>
                <div className="result__foot">
                  <code>{result.id}</code>
                  <span>distance {result.distance?.toFixed(4) ?? "—"}</span>
                </div>
              </article>
            ))}

            {!search.loading && !search.response ? (
              <div className="card empty">
                <h3>一次查询，两路召回，一个排名</h3>
                <p>
                  输入问题后，浏览器先用 INT8 模型生成查询向量，再把向量与原始问题一起发给本地 seekdb。
                  数据库在一次 <code>DBMS_HYBRID_SEARCH</code> 调用里完成全文匹配、HNSW 近邻和 RRF 融合。
                </p>
                <ul>
                  <li>向量化位置：浏览器（WebGPU 优先，WASM 自动降级）</li>
                  <li>向量与原文存储：seekdb <code>VECTOR(512)</code> + <code>FULLTEXT</code> 索引</li>
                  <li>融合排序：数据库内 RRF，客户端不做二次拼合</li>
                </ul>
              </div>
            ) : null}
          </div>

          {search.response?.sql?.length ? (
            <div className="card sql-card">
              <button className="sql-toggle" onClick={() => setShowSql((prev) => !prev)}>
                {showSql ? "收起" : "查看"}数据库内执行的 SQL（{search.response.sql.length} 条）
              </button>
              {showSql ? (
                <div className="sql-list">
                  {search.response.sql.map((statement, index) => (
                    <pre key={index}>{statement.sql}<br />{statement.params ? `-- params: ${JSON.stringify(statement.params)}` : ""}</pre>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </section>

        <section className="col col--right">
          <div className="card">
            <div className="card__head">
              <h2>运行时</h2>
              <span className={`badge ${embedder.backend === "webgpu" ? "badge--gpu" : "badge--wasm"}`}>
                <span className="dot" />
                {embedder.status === "ready" ? (embedder.backend === "webgpu" ? "WebGPU" : "WASM") : "加载中"}
              </span>
            </div>
            <dl className="facts">
              <div><dt>模型</dt><dd>bge-small-zh-v1.5 · {embedder.dtype}</dd></div>
              <div><dt>模型体积</dt><dd>{formatBytes(modelInfo?.model.int8Bytes)} <span className="muted">/ FP32 {formatBytes(modelInfo?.model.fp32Bytes)}</span></dd></div>
              <div><dt>压缩比</dt><dd>{modelInfo?.quantization ? `${modelInfo.quantization.int8Compression}×` : "—"}</dd></div>
              <div><dt>量化保真</dt><dd>cosine 0.9941（FP32 对照）</dd></div>
              <div><dt>模型加载</dt><dd>{formatMs(embedder.modelLoadMs)} <span className="muted">+ 预热 {formatMs(embedder.warmupMs)}</span></dd></div>
              <div><dt>适配器</dt><dd>{embedder.adapter ? `${embedder.adapter.vendor} / ${embedder.adapter.architecture}` : "—"}</dd></div>
              <div><dt>{embedder.backend === "webgpu" ? "跨源隔离" : "WASM 线程"}</dt><dd>{embedder.backend === "webgpu" ? (window.crossOriginIsolated ? "已开启" : "未开启") : `${Math.min(4, navigator.hardwareConcurrency || 2)} · ${window.crossOriginIsolated ? "COOP/COEP ✓" : "单线程"}`}</dd></div>
            </dl>
            {embedder.progress ? (
              <div className="load-progress">
                <div className="progress__bar"><span style={{ width: `${embedder.progress.percent}%` }} /></div>
                <span className="muted">下载模型 {embedder.progress.percent}%</span>
              </div>
            ) : null}
            {embedder.attempts?.length ? (
              <p className="muted small">{embedder.attempts[0]}</p>
            ) : null}
            {embedder.status === "error" ? <p className="error-text">{embedder.error}</p> : null}
            <div className="backend-switch">
              <span className="muted">推理后端</span>
              <div className="segmented segmented--small">
                {(["auto", "webgpu", "wasm"] as const).map((pref) => (
                  <button
                    key={pref}
                    className={embedder.forced === pref ? "is-active" : ""}
                    onClick={() => restart(pref)}
                  >
                    {pref === "auto" ? "自动" : pref.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
            <p className="muted small">
              技术说明：INT8 动态量化（QUInt8，逐通道）来自 FP32 ONNX；WebGPU 不可用时自动降级 WASM，界面会明确提示。
            </p>
          </div>

          <div className="card">
            <div className="card__head">
              <h2>性能基准</h2>
              <button className="button button--ghost" disabled={benchRunning || !runtimeReady} onClick={() => runBench().catch((err) => setError((err as Error).message))}>
                {benchRunning ? "运行中…" : "运行"}
              </button>
            </div>
            <p className="muted small">
              5 条固定查询 × 3 轮，记录查询向量化、seekdb 检索与端到端延迟；首次加载与预热单独报告。
            </p>
            {bench ? (
              <>
                <table className="bench">
                  <tbody>
                    <tr><td>文档向量化</td><td>{bench.docEmbed.chunksPerSec.toFixed(2)} 块/秒</td></tr>
                    <tr><td>查询向量化 P50</td><td>{formatMs(bench.queryEmbedP50Ms)}</td></tr>
                    <tr><td>seekdb 检索 P50</td><td>{formatMs(bench.dbSearchP50Ms)}</td></tr>
                    <tr><td>端到端 P50</td><td>{formatMs(bench.e2eP50Ms)}</td></tr>
                    <tr><td>端到端 P95</td><td>{formatMs(bench.e2eP95Ms)}</td></tr>
                  </tbody>
                </table>
                <div className="bench__foot">
                  <span className="muted">{bench.backend.toUpperCase()} · {bench.queries * bench.rounds} 次查询</span>
                  <button className="link" onClick={exportBench}>导出 JSON</button>
                </div>
              </>
            ) : (
              <p className="muted">运行后会在这里显示实测中位数，并可导出 JSON 作为报告原始数据。</p>
            )}
          </div>

          <div className="card">
            <div className="card__head"><h2>数据库</h2></div>
            <dl className="facts">
              <div><dt>版本</dt><dd>{health?.seekdb.version ?? "—"}</dd></div>
              <div><dt>连接</dt><dd>{health?.seekdb.host ?? "—"} / {health?.seekdb.database ?? "—"}</dd></div>
              <div><dt>健康检查</dt><dd>{health ? formatMs(health.seekdb.latencyMs) : "—"}</dd></div>
              <div><dt>索引</dt><dd>HNSW cosine · IK 全文</dd></div>
            </dl>
            <p className="muted small">
              向量、原文、元数据都在 seekdb；混合检索由数据库完成，前端只负责本地 embedding。
            </p>
          </div>
        </section>
      </main>

      <footer className="footer">
        <span>StepFun 赛道 · seekdb × 端侧 AI 推理</span>
        <span className="muted">本地推理 · 无云端 API · 单次 DB 查询完成融合</span>
      </footer>

      {toast ? <div className="toast">{toast}</div> : null}
    </div>
  );
}
