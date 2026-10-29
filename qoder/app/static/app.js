const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  bootstrap: null,
  health: null,
  answer: null,
  selectedDoc: null,
  view: "ask",
  asking: false,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function shortTime(value) {
  if (!value) return "";
  const text = String(value);
  const match = text.match(/\d{4}-\d{2}-\d{2}[ T](\d{2}:\d{2})/);
  if (match) return match[1];
  return text.length > 16 ? text.slice(0, 16) : text;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let body = null;
  try {
    body = await response.json();
  } catch (_error) {
    body = null;
  }
  if (!response.ok) {
    let detail = body && body.detail;
    if (Array.isArray(detail)) {
      detail = detail.map((item) => item.msg || JSON.stringify(item)).join("；");
    }
    throw new Error(detail || `请求失败（HTTP ${response.status}）`);
  }
  return body;
}

let toastTimer = null;
function toast(message, isError = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error", Boolean(isError));
  node.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add("hidden"), 3600);
}

/* ------------------------------------------------------------------ health */
function renderHealth(health) {
  state.health = health;
  const chips = [];
  const dbOk = health.db === "ok";
  const corpus = health.corpus || {};
  chips.push(
    `<span class="chip ${dbOk ? "ok" : "down"}" title="${escapeHtml(
      dbOk ? `seekdb 已连接 · ${corpus.chunks ?? 0} 个片段` : health.db_error || "seekdb 未连接"
    )}">seekdb${dbOk ? "" : " 离线"}</span>`
  );
  const vectorOk = Boolean(health.vector);
  chips.push(
    `<span class="chip ${vectorOk ? "ok" : "warn"}" title="${escapeHtml(
      health.vector_detail || ""
    )}">向量${vectorOk ? "" : " 不可用"}</span>`
  );
  const llmOk = Boolean(health.llm);
  chips.push(
    `<span class="chip ${llmOk ? "ok" : "warn"}" title="${escapeHtml(
      health.llm_detail || ""
    )}">生成${llmOk ? "" : " 摘录"}</span>`
  );
  const memOk = health.memory === "ok";
  chips.push(
    `<span class="chip ${memOk ? "ok" : "warn"}" title="${escapeHtml(
      health.memory_detail || ""
    )}">记忆${memOk ? "" : " 本地"}</span>`
  );
  $("#health-chips").innerHTML = chips.join("");
}

async function refreshHealth() {
  try {
    renderHealth(await api("/api/health"));
  } catch (error) {
    $("#health-chips").innerHTML = `<span class="chip down">服务未响应</span>`;
  }
}

/* --------------------------------------------------------------- bootstrap */
async function loadBootstrap() {
  try {
    const data = await api("/api/bootstrap");
    state.bootstrap = data;
    renderLibrary();
    renderLibraryView();
    renderMemory(data.memory);
    renderRecent(data.history || []);
    renderSuggestions(data.suggestions || []);
    renderDocFilter();
    $("#library-summary").textContent = data.stats
      ? `${data.stats.documents} 篇 · ${data.stats.chunks} 个片段 · ${data.stats.chars} 字符`
      : data.error
      ? "seekdb 未连接"
      : "资料库为空";
  } catch (error) {
    toast(error.message, true);
    $("#library-summary").textContent = "无法读取资料库";
  }
}

function renderSuggestions(items) {
  const box = $("#suggestions");
  box.innerHTML = items
    .map(
      (question) =>
        `<button class="suggestion" data-q="${escapeHtml(question)}">${escapeHtml(question)}</button>`
    )
    .join("");
  $$(".suggestion", box).forEach((button) => {
    button.addEventListener("click", () => {
      const input = $("#question");
      input.value = button.dataset.q || "";
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    });
  });
}

function renderLibrary() {
  const items = (state.bootstrap && state.bootstrap.documents) || [];
  const box = $("#library-list");
  if (!items.length) {
    box.innerHTML = `<div class="library-empty">还没有资料。点击「导入资料」加入 Markdown / PDF / 笔记。</div>`;
    return;
  }
  box.innerHTML = items
    .map(
      (doc) => `
      <button class="library-item${state.selectedDoc && state.selectedDoc.id === doc.id ? " active" : ""}" data-doc="${doc.id}" data-title="${escapeHtml(doc.title)}">
        <span class="li-title">${escapeHtml(doc.title)}</span>
        <span class="li-meta">${escapeHtml(doc.kind)} · ${doc.chunks} 片段 · ${doc.chars} 字符</span>
      </button>`
    )
    .join("");
  $$(".library-item", box).forEach((button) => {
    button.addEventListener("click", () => {
      const id = Number(button.dataset.doc);
      if (state.selectedDoc && state.selectedDoc.id === id) {
        setDocFilter(null);
      } else {
        setDocFilter({ id, title: button.dataset.title || `资料 ${id}` });
        setView("ask");
      }
      renderLibrary();
    });
  });
}

function setDocFilter(doc) {
  state.selectedDoc = doc;
  renderDocFilter();
  renderLibrary();
}

function renderDocFilter() {
  const node = $("#doc-filter");
  if (!node) return;
  if (!state.selectedDoc) {
    node.classList.add("hidden");
    node.innerHTML = "";
    return;
  }
  node.classList.remove("hidden");
  node.innerHTML = `仅在这篇资料里检索：<strong>${escapeHtml(
    state.selectedDoc.title
  )}</strong> <button class="small" id="clear-doc-filter">取消限定</button>`;
  const clear = $("#clear-doc-filter");
  if (clear) {
    clear.addEventListener("click", () => setDocFilter(null));
  }
}

function renderMemory(snapshot) {
  if (!snapshot) return;
  const badge = $("#memory-source");
  const remote = snapshot.source === "powercontext";
  badge.textContent = remote ? "PowerContext" : "本地暂存";
  badge.className = `badge ${remote ? "badge-powercontext" : "badge-local"}`;
  badge.title = snapshot.error || (remote ? `Scope ${snapshot.scope_id || ""}` : "PowerContext 不可用时的降级存储");

  const profile = snapshot.profile || {};
  const progress = snapshot.progress || {};
  const box = $("#memory-summary");
  if (!profile.topic && !progress.asked) {
    box.innerHTML = `<span class="muted">还没有记忆。点右上角「我的学习记忆」写下研究主题。</span>`;
  } else {
    const rows = [];
    if (profile.topic) rows.push(`<dt>主题</dt><dd class="mem-topic">${escapeHtml(profile.topic)}</dd>`);
    if (profile.goal) rows.push(`<dt>目标</dt><dd>${escapeHtml(profile.goal)}</dd>`);
    if (profile.style) rows.push(`<dt>偏好</dt><dd>${profile.style === "detailed" ? "详细" : "简洁"}</dd>`);
    if (progress.asked) rows.push(`<dt>提问</dt><dd>${progress.asked} 次${progress.last_mode ? ` · 上次 ${escapeHtml(progress.last_mode)}` : ""}</dd>`);
    if (progress.last_question) rows.push(`<dt>上次</dt><dd>${escapeHtml(progress.last_question)}</dd>`);
    if (Array.isArray(progress.weak_topics) && progress.weak_topics.length) {
      rows.push(`<dt>薄弱</dt><dd>${progress.weak_topics.map(escapeHtml).join("；")}</dd>`);
    }
    box.innerHTML = `<dl>${rows.join("")}</dl>`;
  }

  const banner = $("#memory-banner");
  if (profile.topic) {
    const last = progress.last_question ? ` · 上次停在「${escapeHtml(progress.last_question)}」` : "";
    banner.innerHTML = `<strong>我记得：</strong>你在研究 <strong>${escapeHtml(
      profile.topic
    )}</strong>${last}。`;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

function renderRecent(items) {
  const box = $("#recent-list");
  if (!items.length) {
    box.innerHTML = `<div class="library-empty">还没有提问记录。</div>`;
    return;
  }
  box.innerHTML = items
    .map(
      (item) => `
      <button class="recent-item" data-q="${escapeHtml(item.question)}">
        ${escapeHtml(item.question)}
        <small>${shortTime(item.created_at)} · ${escapeHtml(item.mode || "")}${item.confidence ? ` · ${Number(item.confidence).toFixed(2)}` : ""}</small>
      </button>`
    )
    .join("");
  $$(".recent-item", box).forEach((button) => {
    button.addEventListener("click", () => {
      const input = $("#question");
      input.value = button.dataset.q || "";
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    });
  });
}

/* --------------------------------------------------------------------- ask */
function citeButtons(escaped, maxN) {
  return escaped.replace(/\[(\d{1,2})\]/g, (match, number) => {
    const n = Number(number);
    if (!Number.isFinite(n) || n < 1 || n > maxN) return match;
    return `<button class="cite" data-n="${n}" title="查看第 ${n} 条证据">[${n}]</button>`;
  });
}

function renderBody(text, maxN) {
  const lines = String(text || "").split("\n");
  const html = [];
  let inList = false;
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
      continue;
    }
    if (trimmed.startsWith("- ") || trimmed.startsWith("• ")) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${citeButtons(escapeHtml(trimmed.slice(2)), maxN)}</li>`);
    } else {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
      html.push(`<p>${citeButtons(escapeHtml(trimmed), maxN)}</p>`);
    }
  }
  if (inList) html.push("</ul>");
  return html.join("") || "<p></p>";
}

function modeLabel(mode) {
  if (mode === "llm") return "模型作答";
  if (mode === "extractive") return "原文摘录";
  if (mode === "insufficient") return "资料不足";
  return mode || "回答";
}

function renderAssistantMessage(data) {
  const maxN = (data.evidence || []).length;
  const wrapper = document.createElement("div");
  wrapper.className = "msg msg-assistant";
  const memoryLabel = data.memory && data.memory.source === "powercontext" ? "PowerContext" : "本地暂存";
  const embedWarn = data.embedding && data.embedding.error
    ? `<span class="reason">向量不可用，本次仅全文检索：${escapeHtml(data.embedding.error)}</span>`
    : "";
  const reason = data.reason
    ? `<span class="reason">${escapeHtml(data.reason)}</span>`
    : `<span class="reason">${embedWarn || "只依据检索到的原文作答"}</span>`;
  wrapper.innerHTML = `
    <div class="avatar" aria-hidden="true">典</div>
    <div class="bubble-assistant">
      <div class="answer-head">
        <span class="mode-badge mode-${escapeHtml(data.mode)}">${escapeHtml(modeLabel(data.mode))}</span>
        <span class="meta">置信度 ${Number(data.confidence || 0).toFixed(2)} · ${data.elapsed_ms ?? 0} ms · 记忆 ${memoryLabel}</span>
      </div>
      <div class="answer-body">${renderBody(data.answer, maxN)}</div>
      <div class="answer-foot">
        ${reason}
        <span class="answer-actions">
          ${data.mode === "insufficient" ? "" : `<button data-action="note">收进笔记</button>`}
        </span>
      </div>
    </div>`;
  const noteButton = $('[data-action="note"]', wrapper);
  if (noteButton) {
    noteButton.addEventListener("click", () => saveNote(noteButton, data));
  }
  return wrapper;
}

function appendUserMessage(question) {
  const node = document.createElement("div");
  node.className = "msg msg-user";
  node.innerHTML = `<div class="bubble">${escapeHtml(question)}</div>`;
  $("#messages").appendChild(node);
}

async function ask(question) {
  if (state.asking) return;
  const trimmed = String(question || "").trim();
  if (!trimmed) {
    toast("先写一个问题", true);
    return;
  }
  const welcome = $(".welcome");
  if (welcome) welcome.remove();
  state.asking = true;
  $("#ask-btn").disabled = true;
  appendUserMessage(trimmed);

  const thinking = document.createElement("div");
  thinking.className = "msg msg-assistant";
  thinking.innerHTML = `<div class="avatar" aria-hidden="true">典</div><div class="bubble-assistant"><div class="thinking">正在检索资料并核对引用…</div></div>`;
  $("#messages").appendChild(thinking);
  $("#messages").scrollTop = $("#messages").scrollHeight;

  try {
    const payload = {
      question: trimmed,
      style: $("#detailed").checked ? "detailed" : "concise",
    };
    if (state.selectedDoc) payload.doc_id = state.selectedDoc.id;
    const data = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.answer = data;
    thinking.replaceWith(renderAssistantMessage(data));
    renderEvidence(data.evidence || [], data.trace || {});
    const input = $("#question");
    input.value = "";
    autoGrow(input);
    if (state.bootstrap) {
      state.bootstrap.history = [
        {
          id: `local-${Date.now()}`,
          question: trimmed,
          mode: data.mode,
          confidence: data.confidence,
          created_at: new Date().toISOString(),
        },
        ...(state.bootstrap.history || []),
      ].slice(0, 12);
      renderRecent(state.bootstrap.history);
    }
    api("/api/memory")
      .then((snapshot) => {
        renderMemory(snapshot);
        if (state.bootstrap) state.bootstrap.memory = snapshot;
      })
      .catch(() => {});
  } catch (error) {
    thinking.innerHTML = `<div class="avatar" aria-hidden="true">典</div><div class="bubble-assistant"><div class="answer-body"><p>${escapeHtml(
      error.message
    )}</p></div><div class="reason">请确认 seekdb 已启动，或稍后重试。</div></div>`;
    toast(error.message, true);
  } finally {
    state.asking = false;
    $("#ask-btn").disabled = false;
    $("#messages").scrollTop = $("#messages").scrollHeight;
  }
}

async function saveNote(button, data) {
  button.disabled = true;
  try {
    await api("/api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: data.question,
        answer: data.answer,
        citations: data.citations || [],
      }),
    });
    button.textContent = "已收藏";
    toast("已收进笔记");
  } catch (error) {
    button.disabled = false;
    toast(error.message, true);
  }
}

/* ---------------------------------------------------------------- evidence */
function renderEvidence(evidence, trace) {
  const count = evidence.length;
  const used = evidence.filter((row) => row.used).length;
  $("#evidence-count").textContent = count ? `${count} 条 · ${used} 条被引用` : "没有证据";
  const box = $("#evidence-list");
  if (!count) {
    box.innerHTML = `<div class="empty-evidence"><p>这次没有检索到可用片段。</p><p class="muted small-text">可以换一种问法，或导入相关资料。</p></div>`;
    renderTrace(trace || {});
    return;
  }
  box.innerHTML = evidence
    .map((row) => {
      const hasRrf = row.rrf !== null && row.rrf !== undefined;
      const ranks = [
        `<span class="pill pill-vec${row.vec_rank ? "" : " miss"}">${
          row.vec_rank ? `向量 #${row.vec_rank}` : "向量 —"
        }</span>`,
        `<span class="pill pill-ft${row.ft_rank ? "" : " miss"}">${
          row.ft_rank ? `全文 #${row.ft_rank}` : "全文 —"
        }</span>`,
        `<span class="pill pill-rrf${hasRrf ? "" : " miss"}">${
          hasRrf ? `RRF ${Number(row.rrf).toFixed(6)}` : "RRF —"
        }</span>`,
      ];
      if (row.vec_sim !== null && row.vec_sim !== undefined) {
        ranks.push(`<span class="pill">相似度 ${Number(row.vec_sim).toFixed(3)}</span>`);
      }
      const heading = row.heading ? ` › ${escapeHtml(row.heading)}` : "";
      const page = row.page ? ` · 第 ${row.page} 页` : "";
      return `
        <article class="evidence-card${row.used ? " used" : ""}" data-n="${row.n}" tabindex="-1">
          <div class="ev-head">
            <span class="ev-n">${row.n}</span>
            <div class="ev-title">
              <strong>${escapeHtml(row.doc_title || "未命名资料")}</strong>
              <span>${escapeHtml(row.source || "")}${heading}${page}</span>
            </div>
          </div>
          <div class="ev-rank">${ranks.join("")}</div>
          <div class="evidence-text">${escapeHtml(row.text || "")}</div>
          <div class="ev-actions"><button data-action="expand">查看完整原文</button></div>
        </article>`;
    })
    .join("");
  $$(".evidence-card", box).forEach((card) => {
    const expand = $('[data-action="expand"]', card);
    const toggle = () => {
      card.classList.toggle("expanded");
      if (expand) {
        expand.textContent = card.classList.contains("expanded") ? "收起原文" : "查看完整原文";
      }
    };
    if (expand) {
      expand.addEventListener("click", (event) => {
        event.stopPropagation();
        toggle();
      });
    }
    card.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      toggle();
    });
  });
  renderTrace(trace || {});
}

function traceTable(rows) {
  if (!Array.isArray(rows) || !rows.length) {
    return `<p class="muted">没有命中。</p>`;
  }
  const body = rows
    .map(
      (row) => `<tr>
        <td>${row.rank}</td>
        <td>${row.chunk_id}</td>
        <td class="snip" title="${escapeHtml(row.doc_title || "")}">${escapeHtml(row.doc_title || "")}</td>
        <td>${row.vec_sim === null || row.vec_sim === undefined ? "—" : Number(row.vec_sim).toFixed(3)}</td>
        <td>${row.ft_score === null || row.ft_score === undefined ? "—" : Number(row.ft_score).toFixed(3)}</td>
        <td>${row.rrf === null || row.rrf === undefined ? "—" : Number(row.rrf).toFixed(6)}</td>
      </tr>`
    )
    .join("");
  return `<table><thead><tr><th>#</th><th>chunk</th><th>文档</th><th>vec</th><th>ft</th><th>RRF</th></tr></thead><tbody>${body}</tbody></table>`;
}

function renderTrace(trace) {
  const body = $("#trace-body");
  const vector = trace.vector || [];
  const fulltext = trace.fulltext || [];
  if (!vector.length && !fulltext.length) {
    body.innerHTML = `<span class="muted">暂无检索。</span>`;
    return;
  }
  body.innerHTML = `
    <h4>向量召回 · cosine_distance</h4>
    ${traceTable(vector)}
    <h4>全文召回 · MATCH ... AGAINST</h4>
    ${traceTable(fulltext)}`;
}

function highlightEvidence(n) {
  const card = document.querySelector(`.evidence-card[data-n="${n}"]`);
  if (!card) {
    toast(`没有第 ${n} 条证据`, true);
    return;
  }
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  card.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
  card.focus({ preventScroll: true });
  card.classList.add("flash");
  card.classList.remove("expanded");
  const expand = $('[data-action="expand"]', card);
  if (expand) expand.textContent = "查看完整原文";
  window.setTimeout(() => card.classList.remove("flash"), 2400);
}

/* ------------------------------------------------------------------ library */
function renderLibraryView() {
  const data = state.bootstrap;
  if (!data) return;
  const stats = data.stats;
  $("#library-stats").innerHTML = stats
    ? [
        ["资料", stats.documents, "篇"],
        ["片段", stats.chunks, "个"],
        ["字符", stats.chars, "字"],
        ["平均片段", stats.avg_chunk_chars, "字"],
      ]
        .map(
          ([label, value, unit]) =>
            `<div class="metric"><span>${label}</span><strong>${value ?? 0}</strong><em>${unit}</em></div>`
        )
        .join("")
    : `<div class="metric"><span>seekdb</span><strong>离线</strong><em>资料不可读</em></div>`;

  const items = data.documents || [];
  const table = $("#library-table");
  if (!items.length) {
    table.innerHTML = `<div class="empty-evidence"><p>资料库为空。</p><p class="muted small-text">导出一份 Markdown 或 PDF 试试。</p></div>`;
    return;
  }
  table.innerHTML = `
    <table class="grid">
      <thead><tr><th>资料</th><th>来源</th><th>类型</th><th>片段</th><th>字符</th><th>操作</th></tr></thead>
      <tbody>
        ${items
          .map(
            (doc) => `<tr>
              <td class="wrap"><strong>${escapeHtml(doc.title)}</strong></td>
              <td class="wrap"><code>${escapeHtml(doc.source)}</code></td>
              <td>${escapeHtml(doc.kind)}</td>
              <td class="num">${doc.chunks}</td>
              <td class="num">${doc.chars}</td>
              <td><div class="row-actions"><button class="small danger" data-delete="${doc.id}" data-title="${escapeHtml(doc.title)}">移除</button></div></td>
            </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
  $$("[data-delete]", table).forEach((button) => {
    button.addEventListener("click", () => deleteDocument(button));
  });
}

async function deleteDocument(button) {
  const id = Number(button.dataset.delete);
  const title = button.dataset.title || `资料 ${id}`;
  if (!window.confirm(`确定移除《${title}》及其全部片段吗？问答历史与笔记会保留。`)) return;
  button.disabled = true;
  try {
    await api("/api/library/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc_id: id }),
    });
    if (state.selectedDoc && state.selectedDoc.id === id) setDocFilter(null);
    toast(`已移除《${title}》`);
    await loadBootstrap();
  } catch (error) {
    button.disabled = false;
    toast(error.message, true);
  }
}

/* --------------------------------------------------------------------- eval */
function percent(value) {
  if (value === null || value === undefined) return "—";
  return `${(Number(value) * 100).toFixed(0)}%`;
}

async function loadEval() {
  const summary = $("#eval-summary");
  const note = $("#eval-note");
  const table = $("#eval-table");
  summary.innerHTML = "";
  note.textContent = "正在读取评测结果…";
  table.innerHTML = "";
  try {
    const data = await api("/api/eval");
    if (!data.available) {
      note.textContent = data.message || "尚未生成评测结果。";
      return;
    }
    const s = data.summary || {};
    note.textContent = data.generated_at
      ? `生成于 ${data.generated_at}${data.model ? ` · 向量模型 ${data.model}` : ""}${data.top_k ? ` · Recall@${data.top_k}` : ""}`
      : "";
    summary.innerHTML = [
      ["Recall@5", percent(s.recall_at_5), `${s.recall_hits ?? 0}/${s.answerable ?? 0}`],
      ["引用命中", percent(s.citation_hit), `${s.citation_hits ?? 0}/${s.answerable ?? 0}`],
      ["拒答准确", percent(s.refusal_accuracy), `${s.refusal_hits ?? 0}/${s.unanswerable ?? 0}`],
      ["中位耗时", s.p50_ms ?? "—", "ms"],
      ["平均耗时", s.mean_ms ?? "—", "ms"],
    ]
      .map(
        ([label, value, unit]) =>
          `<div class="metric"><span>${label}</span><strong>${value}</strong><em>${unit}</em></div>`
      )
      .join("");
    const rows = data.questions || [];
    if (!rows.length) {
      table.innerHTML = `<div class="empty-evidence"><p>评测结果里没有逐题记录。</p></div>`;
      return;
    }
    table.innerHTML = `
      <table class="grid">
        <thead><tr><th>#</th><th>问题</th><th>类型</th><th>模式</th><th>Recall@5</th><th>引用</th><th>拒答</th><th>耗时</th></tr></thead>
        <tbody>
          ${rows
            .map((row) => {
              const answerable = row.answerable !== false;
              const recallOk = row.recall_at_5 === true;
              const citeOk = row.citation_hit === true;
              const refusalOk = row.refused === true;
              return `<tr>
                <td>${escapeHtml(row.id || "")}</td>
                <td class="wrap">${escapeHtml(row.question || "")}</td>
                <td>${answerable ? "可答" : "资料外"}</td>
                <td>${escapeHtml(row.mode || "")}</td>
                <td><span class="status-dot ${answerable ? (recallOk ? "" : "bad") : ""}"></span>${
                answerable ? (recallOk ? "命中" : "未命中") : "—"
              }</td>
                <td>${
                  answerable
                    ? `<span class="status-dot ${citeOk ? "" : "bad"}"></span>${citeOk ? "有" : "无"}`
                    : `<span class="muted">—</span>`
                }</td>
                <td>${
                  answerable
                    ? `<span class="muted">—</span>`
                    : `<span class="status-dot ${refusalOk ? "" : "bad"}"></span>${
                        refusalOk ? "已拒答" : "未拒答"
                      }`
                }</td>
                <td class="num">${row.elapsed_ms ?? "—"} ms</td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>`;
  } catch (error) {
    note.textContent = error.message;
  }
}

/* -------------------------------------------------------------------- view */
function setView(view) {
  state.view = view;
  $$(".tab").forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
    tab.tabIndex = active ? 0 : -1;
  });
  $$(".view").forEach((node) => node.classList.add("hidden"));
  const target = $(`#view-${view}`);
  if (target) target.classList.remove("hidden");
  const workspace = $(".workspace");
  if (workspace) workspace.classList.toggle("no-evidence", view !== "ask");
  if (view === "library") renderLibraryView();
  if (view === "eval") loadEval();
}

/* ------------------------------------------------------------------ memory */
function openMemory() {
  const snapshot = (state.bootstrap && state.bootstrap.memory) || {};
  const profile = snapshot.profile || {};
  $("#profile-topic").value = profile.topic || "";
  $("#profile-style").value = profile.style === "detailed" ? "detailed" : "concise";
  $("#profile-goal").value = profile.goal || "";
  $("#memory-status").textContent = snapshot.source === "powercontext" ? "当前记忆在 PowerContext。" : "当前记忆在本地暂存。";
  $("#memory-modal").classList.remove("hidden");
  $("#profile-topic").focus();
}

function closeMemory() {
  $("#memory-modal").classList.add("hidden");
}

async function saveMemory(event) {
  event.preventDefault();
  const topic = $("#profile-topic").value.trim();
  if (!topic) {
    toast("研究主题不能为空", true);
    return;
  }
  const status = $("#memory-status");
  status.textContent = "正在写入…";
  try {
    const result = await api("/api/memory/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        topic,
        style: $("#profile-style").value,
        goal: $("#profile-goal").value.trim(),
      }),
    });
    status.textContent =
      result.source === "powercontext" ? "已写入 PowerContext。" : "PowerContext 不可用，已写入本地暂存。";
    toast(result.source === "powercontext" ? "记忆已写入 PowerContext" : "已写入本地暂存");
    if (state.bootstrap) {
      state.bootstrap.memory = {
        ...(state.bootstrap.memory || {}),
        source: result.source,
        profile: result.profile,
      };
      renderMemory(state.bootstrap.memory);
    }
    const snapshot = await api("/api/memory");
    if (state.bootstrap) state.bootstrap.memory = snapshot;
    renderMemory(snapshot);
    window.setTimeout(closeMemory, 600);
  } catch (error) {
    status.textContent = error.message;
    toast(error.message, true);
  }
}

/* ------------------------------------------------------------------ import */
async function importFile(file) {
  if (!file) return;
  if (file.size > 20 * 1024 * 1024) {
    toast("文件超过 20MB 限制", true);
    return;
  }
  const form = new FormData();
  form.append("file", file);
  toast(`正在导入并向量化《${file.name}》…`);
  try {
    const result = await api("/api/import", { method: "POST", body: form });
    toast(`已导入《${result.title}》：${result.chunks} 个片段`);
    await loadBootstrap();
    setView("library");
  } catch (error) {
    toast(error.message, true);
  } finally {
    $("#import-input").value = "";
  }
}

/* ------------------------------------------------------------------- init */
function autoGrow(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 140)}px`;
}

function bind() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => setView(tab.dataset.view));
    tab.addEventListener("keydown", (event) => {
      const tabs = $$(".tab");
      const index = tabs.indexOf(tab);
      let next = null;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next === null) return;
      event.preventDefault();
      tabs[next].focus();
      setView(tabs[next].dataset.view);
    });
  });

  $$(".panel-toggle").forEach((button) => {
    button.addEventListener("click", () => {
      const panel = button.closest(".panel");
      if (!panel) return;
      const collapsed = panel.classList.toggle("collapsed");
      const title = ($("h2", panel) && $("h2", panel).textContent.trim()) || "面板";
      button.setAttribute("aria-expanded", collapsed ? "false" : "true");
      button.setAttribute("aria-label", `${collapsed ? "展开" : "收起"}${title}`);
    });
  });

  $("#ask-form").addEventListener("submit", (event) => {
    event.preventDefault();
    ask($("#question").value);
  });
  $("#question").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#ask-form").requestSubmit();
    }
  });
  $("#question").addEventListener("input", (event) => autoGrow(event.target));

  $("#messages").addEventListener("click", (event) => {
    const cite = event.target.closest(".cite");
    if (cite) {
      highlightEvidence(Number(cite.dataset.n));
    }
  });

  $("#memory-btn").addEventListener("click", openMemory);
  $("#memory-close").addEventListener("click", closeMemory);
  $("#memory-cancel").addEventListener("click", closeMemory);
  $("#memory-form").addEventListener("submit", saveMemory);
  $("#memory-modal").addEventListener("click", (event) => {
    if (event.target === $("#memory-modal")) closeMemory();
  });

  const input = $("#import-input");
  $("#import-btn").addEventListener("click", () => input.click());
  $("#import-btn-2").addEventListener("click", () => input.click());
  input.addEventListener("change", () => importFile(input.files && input.files[0]));

  $("#eval-refresh").addEventListener("click", loadEval);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#memory-modal").classList.contains("hidden")) {
      closeMemory();
    }
  });
}

async function init() {
  bind();
  await Promise.all([refreshHealth(), loadBootstrap()]);
  setView("ask");
}

init();
