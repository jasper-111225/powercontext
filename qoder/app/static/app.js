/* 据典 · 前端（无构建步骤，单文件） */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const S = {
  boot: null,
  memory: null,
  current: null,
  busy: false,
  view: "ask",
  docs: [],
};

/* ------------------------------------------------------------------ utils */
function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function toast(message, ms = 2200) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => (el.hidden = true), ms);
}

function fmt(n) {
  return Number(n || 0).toLocaleString("zh-CN");
}

function size(n) {
  if (n > 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
  if (n > 1024) return (n / 1024).toFixed(1) + " KB";
  return n + " B";
}

function citeChip(n) {
  return `<button class="cite" data-cite="${n}" title="查看第 ${n} 条证据">${n}</button>`;
}

function markTerms(text, terms) {
  let html = esc(text);
  (terms || [])
    .filter((t) => t && t.length >= 2)
    .sort((a, b) => b.length - a.length)
    .slice(0, 8)
    .forEach((term) => {
      const safe = esc(term).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      html = html.replace(new RegExp(safe, "gi"), (m) => `<mark>${m}</mark>`);
    });
  return html;
}

/* ------------------------------------------------------------- answer html */
function answerHtml(text, citations) {
  const allowed = new Set(citations || []);
  const lines = esc(text).split(/\n+/);
  const out = [];
  let list = [];

  const inline = (line) =>
    line
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\[(\d{1,2})\]/g, (m, n) => (allowed.has(Number(n)) ? citeChip(n) : ""));

  const flushList = () => {
    if (list.length) {
      out.push(`<ul>${list.map((li) => `<li>${inline(li)}</li>`).join("")}</ul>`);
      list = [];
    }
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (/^[-*•]\s+/.test(line)) {
      list.push(line.replace(/^[-*•]\s+/, ""));
    } else if (/^\d+[.、]\s+/.test(line)) {
      list.push(line.replace(/^\d+[.、]\s+/, ""));
    } else {
      flushList();
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  flushList();
  return out.join("");
}

/* ---------------------------------------------------------------- bootstrap */
async function loadBootstrap() {
  const data = await fetch("/api/bootstrap").then((r) => r.json());
  S.boot = data;
  S.docs = data.documents || [];
  S.memory = data.memory || {};
  renderLibrary();
  renderHistory(data.history || []);
  renderMemory();
  renderSuggestions(data.suggestions || []);
  $("#hero-docs").textContent = fmt(data.corpus?.documents);
  $("#hero-chunks").textContent = fmt(data.corpus?.chunks);
  $("#doc-count").textContent = fmt(data.corpus?.documents);
  $("#ready-hint").textContent = data.corpus?.chunks
    ? `资料库 ${fmt(data.corpus.documents)} 篇 / ${fmt(data.corpus.chunks)} 个片段`
    : "资料库为空，先导入资料";
}

function renderSuggestions(items) {
  $("#suggestions").innerHTML = items
    .map((q) => `<button class="chip" data-ask="${esc(q)}">${esc(q)}</button>`)
    .join("");
}

function renderHistory(items) {
  const box = $("#history-list");
  if (!items.length) {
    box.innerHTML = `<div class="hint">还没有提问记录。</div>`;
    return;
  }
  box.innerHTML = items
    .slice(0, 10)
    .map(
      (item) =>
        `<button class="history-item" data-ask="${esc(item.question)}" title="${esc(item.question)}">${esc(
          item.question
        )}</button>`
    )
    .join("");
}

function renderLibrary() {
  const docs = S.docs || [];
  $("#doc-list").innerHTML =
    docs
      .map(
        (doc) =>
          `<button class="doc-item" data-doc="${doc.id}" title="${esc(doc.title)}">
             <span class="dot ${esc(doc.kind)}"></span>
             <span class="t">${esc(doc.title)}</span>
             <span class="n">${fmt(doc.chunks)}</span>
           </button>`
      )
      .join("") || `<div class="hint">资料库还是空的。</div>`;
}

/* ------------------------------------------------------------------ memory */
function renderMemory() {
  const memory = S.memory || {};
  const profile = memory.profile || {};
  const progress = memory.progress || {};
  const topic = profile.topic || "";
  const style = profile.style === "detailed" ? "详细" : "简洁";

  // 侧栏
  const bits = [];
  if (topic) bits.push(`研究 <b>${esc(topic)}</b>`);
  if (profile.goal) bits.push(`目标：${esc(profile.goal)}`);
  if (progress.asked) bits.push(`已问 ${progress.asked} 次`);
  if (progress.last_question) bits.push(`上次：${esc(progress.last_question.slice(0, 34))}`);
  $("#memory-mini-body").innerHTML = bits.length
    ? bits.map((b) => `<div>${b}</div>`).join("") +
      `<div><span class="memory-pill">${style}回答</span><span class="memory-pill">${
        memory.connected ? "PowerContext" : "本地暂存"
      }</span></div>`
    : `<span class="none">还没有记忆。设置研究主题后，我会在下次见面时记得。</span>`;

  // 顶部横幅
  const banner = $("#memory-banner");
  if (topic || progress.last_question) {
    banner.hidden = false;
    banner.innerHTML = `
      <span>我记得：${topic ? `你在研究 <b>${esc(topic)}</b>` : "你在这里学习"}。</span>
      ${progress.last_question ? `<span>上次停在 <b>${esc(progress.last_question.slice(0, 46))}</b></span>` : ""}
      <button id="banner-continue">继续上次</button>
      <button id="banner-edit">调整记忆</button>`;
    $("#banner-continue").onclick = () => {
      if (progress.last_question) ask(progress.last_question);
      else $("#question").focus();
    };
    $("#banner-edit").onclick = openMemory;
  } else {
    banner.hidden = true;
  }

  // 记忆弹窗
  $("#m-topic").value = topic;
  $("#m-goal").value = profile.goal || "";
  $$('input[name="m-style"]').forEach((el) => (el.checked = el.value === (profile.style || "concise")));
  $("#m-asked").textContent = progress.asked || 0;
  $("#m-entries").textContent = memory.entries || 0;
  $("#m-source").textContent = memory.connected ? "PowerContext" : "本地暂存";
  $("#m-source").className = memory.connected ? "ok" : "muted";
  $("#m-last").textContent = progress.last_question || "还没有记录";
  const weak = progress.weak_topics || [];
  $("#m-weak").innerHTML = weak.length
    ? weak.map((t) => `<li>${esc(t)}</li>`).join("")
    : "<li class='muted'>暂无，继续提问会出现。</li>";
  $("#memory-source").textContent = memory.connected
    ? `已连接 PowerContext（Scope ${memory.scope_id || "—"}）· 重启 Server 后记忆仍在`
    : `PowerContext 未连接：${memory.error || "请先启动"}（当前写入本地 .runtime）`;
  const entries = memory.recent || [];
  $("#m-entries-list").innerHTML = entries.length
    ? entries
        .map(
          (entry) =>
            `<div class="memory-entry"><code>${esc(entry.kind)}</code><span title="${esc(
              entry.text
            )}">${esc(memoryEntryLabel(entry))}</span></div>`
        )
        .join("")
    : `<div class="hint">还没有记忆条目。</div>`;
}

function memoryEntryLabel(entry) {
  try {
    const value = JSON.parse(entry.text);
    if (value.topic) return `研究：${value.topic}`;
    if (value.last_question) return `上次：${value.last_question}`;
    return JSON.stringify(value);
  } catch {
    return entry.text;
  }
}

function openMemory() {
  renderMemory();
  $("#memory-dialog").showModal();
}

async function saveProfile() {
  const topic = $("#m-topic").value.trim();
  const goal = $("#m-goal").value.trim();
  const style = $$('input[name="m-style"]').find((el) => el.checked)?.value || "concise";
  const response = await fetch("/api/memory/profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic, style, goal }),
  }).then((r) => r.json());
  S.memory = response.snapshot || S.memory;
  renderMemory();
  toast(response.source === "powercontext" ? "已写入 PowerContext 记忆" : "PowerContext 未连接，已本地暂存");
}

/* --------------------------------------------------------------- ask flow */
async function ask(question) {
  if (S.busy) return;
  question = (question || "").trim();
  if (!question) return;
  if (!S.boot?.corpus?.chunks) {
    toast("资料库为空，请先导入资料");
    return;
  }
  S.busy = true;
  $("#send").disabled = true;
  $("#hero").style.display = "none";

  const skeleton = document.createElement("article");
  skeleton.className = "answer-card";
  skeleton.id = "pending";
  skeleton.innerHTML = `<div class="q-row"><span class="q-mark">问</span><h3>${esc(question)}</h3></div>
    <div class="skeleton">正在检索证据并组织答案</div>`;
  $("#thread").appendChild(skeleton);
  skeleton.scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        style: $("#style-detailed").checked ? "detailed" : "concise",
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "请求失败");
    skeleton.remove();
    S.current = data;
    renderAnswer(data);
    renderEvidence(data);
    if (data.memory?.source) {
      // 记忆快照可能已变化，轻量刷新一次
      fetch("/api/memory")
        .then((r) => r.json())
        .then((m) => {
          S.memory = m;
          renderMemory();
        });
    }
    fetch("/api/history")
      .then((r) => r.json())
      .then(renderHistory);
  } catch (error) {
    skeleton.remove();
    toast(error.message || "提问失败");
    if (!S.current) $("#hero").style.display = "";
  } finally {
    S.busy = false;
    $("#send").disabled = false;
  }
}

function renderAnswer(data) {
  const card = document.createElement("article");
  card.className = "answer-card";
  const modeLabel = {
    llm: "模型改写 · 引用已核验",
    extractive: "原文摘录 · 逐句可核验",
    insufficient: "资料不足 · 拒绝作答",
  }[data.mode] || data.mode;
  const conf = Math.round((data.confidence || 0) * 100);
  const confClass = conf < 35 ? "bad" : conf < 55 ? "low" : "";
  const seconds = ((data.timings?.total_ms || 0) / 1000).toFixed(1);

  card.innerHTML = `
    <div class="q-row"><span class="q-mark">问</span><h3>${esc(data.question)}</h3></div>
    <div class="answer-meta">
      <span class="badge ${esc(data.mode)}">${esc(modeLabel)}</span>
      <span class="conf ${confClass}"><span class="bar"><i style="width:${conf}%"></i></span><b>${conf}%</b> 证据置信度</span>
      <span>${seconds}s · ${data.evidence?.length || 0} 条证据</span>
      <span class="muted">${data.style === "detailed" ? "详细模式" : "简洁模式"}</span>
    </div>
    <div class="answer-body">${answerHtml(data.answer, data.citations)}</div>
    <div class="answer-actions">
      <button data-act="note">＋ 收进笔记</button>
      <button data-act="copy">复制答案</button>
      <button data-act="trace">看检索轨迹</button>
      <span class="spacer"></span>
      <span class="stamp">query #${data.query_id} · ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}</span>
    </div>
    ${
      data.reason
        ? `<details class="answer-note"><summary>为什么这样作答</summary><p>${esc(data.reason)}</p></details>`
        : ""
    }`;

  card.querySelector('[data-act="copy"]').onclick = async () => {
    await navigator.clipboard.writeText(data.answer.replace(/\[(\d+)\]/g, "[$1]"));
    toast("答案已复制");
  };
  card.querySelector('[data-act="note"]').onclick = async () => {
    const response = await fetch("/api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: data.question,
        answer: data.answer,
        citations: data.evidence.filter((e) => e.used),
      }),
    });
    if (response.ok) toast("已收进笔记");
    else toast("保存失败");
  };
  card.querySelector('[data-act="trace"]').onclick = () => {
    $("#trace-box").open = true;
    $("#trace-box").scrollIntoView({ behavior: "smooth", block: "nearest" });
  };
  card.addEventListener("click", (event) => {
    const chip = event.target.closest(".cite");
    if (chip) focusEvidence(Number(chip.dataset.cite));
  });
  $("#thread").appendChild(card);
  card.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------------------------------------------------------------- evidence */
function renderEvidence(data) {
  const box = $("#evidence-list");
  const items = data.evidence || [];
  $("#evidence-count").textContent = items.length;
  if (!items.length) {
    box.innerHTML = `<div class="empty small">这次没有召回到证据。</div>`;
    return;
  }
  box.innerHTML = items
    .map((item) => {
      const ranks = [];
      if (item.vec_rank) ranks.push(`<span class="rank vec">向量 #${item.vec_rank}</span>`);
      if (item.ft_rank) ranks.push(`<span class="rank ft">全文 #${item.ft_rank}</span>`);
      ranks.push(`<span class="rank rrf">RRF ${Number(item.rrf).toFixed(4)}</span>`);
      return `<article class="ev-card ${item.used ? "used" : ""}" data-ev="${item.index}" id="ev-${item.index}">
        <div class="ev-top">
          <span class="ev-index">${item.index}</span>
          <div>
            <h4>${esc(item.doc_title)}</h4>
            <p>${esc(item.heading || "正文")}${item.page ? ` · 第 ${item.page} 页` : ""}</p>
          </div>
        </div>
        <p class="ev-snippet">${markTerms(item.snippet, data.terms)}</p>
        <div class="ev-ranks">${ranks.join("")}</div>
        <button class="open-full" data-full="${item.index}">查看完整原文 ↗</button>
      </article>`;
    })
    .join("");

  box.querySelectorAll("[data-full]").forEach((button) => {
    button.onclick = (event) => {
      event.stopPropagation();
      openChunk(Number(button.dataset.full));
    };
  });
  box.querySelectorAll(".ev-card").forEach((card) => {
    card.addEventListener("click", () => openChunk(Number(card.dataset.ev)));
  });
  renderTrace(data);
}

function focusEvidence(index) {
  const panel = $("#evidence-panel");
  if (window.innerWidth <= 1020) {
    openChunk(index);
    return;
  }
  const card = $(`#ev-${index}`);
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  card.classList.add("flash");
  setTimeout(() => card.classList.remove("flash"), 1200);
}

function openChunk(index) {
  const item = S.current?.evidence?.find((e) => e.index === index);
  if (!item) return;
  $("#chunk-kicker").textContent = `EVIDENCE ${item.index} · ${item.doc_title}`;
  $("#chunk-title").textContent = item.heading || "原文片段";
  $("#chunk-meta").innerHTML = [
    `向量排名 ${item.vec_rank ?? "—"}`,
    `全文排名 ${item.ft_rank ?? "—"}`,
    `相似度 ${item.vec_sim ?? "—"}`,
    `RRF ${item.rrf}`,
    item.page ? `第 ${item.page} 页` : "",
  ]
    .filter(Boolean)
    .map((x) => `<span>${esc(x)}</span>`)
    .join("");
  $("#chunk-text").textContent = item.text;
  $("#chunk-dialog").showModal();
}

function renderTrace(data) {
  const trace = data.trace || {};
  const row = (item, value, key) => `
    <div class="trace-row">
      <span class="r">${item.rank}</span>
      <span class="s" title="${esc(item.heading || "")}">${esc(item.heading || item.doc_title || "—")}</span>
      <span class="muted">${key === "vec" ? Number(item.vec_sim).toFixed(3) : Number(item.ft_score).toFixed(2)}</span>
    </div>`;
  $("#trace-body").innerHTML = `
    <div class="trace-col">
      <h5>向量近邻 · cosine</h5>
      ${(trace.vector || []).map((x) => row(x, null, "vec")).join("") || "<div class='hint'>无</div>"}
    </div>
    <div class="trace-col">
      <h5>全文召回 · IK</h5>
      ${(trace.fulltext || []).map((x) => row(x, null, "ft")).join("") || "<div class='hint'>无</div>"}
    </div>
    <div class="trace-formula">score(d) = 1/(${trace.params?.rrf_k || 60} + rank_vector) + 1/(${trace.params?.rrf_k || 60} + rank_fulltext)</div>`;
}

/* ------------------------------------------------------------------ library */
function renderLibraryPage() {
  if (!S.boot) return;
  const stats = S.boot.corpus || {};
  $("#library-stats").innerHTML = [
    ["资料", fmt(stats.documents), "documents"],
    ["片段", fmt(stats.chunks), "chunks"],
    ["字符", fmt(stats.chars), "characters"],
    ["平均片段", fmt(stats.avg_chunk_chars), "chars / chunk"],
  ]
    .map(([label, value, sub]) => `<div class="stat"><b>${value}</b><span>${label} · ${sub}</span></div>`)
    .join("");

  $("#doc-table-body").innerHTML =
    (S.docs || [])
      .map(
        (doc) => `<tr>
          <td class="title">${esc(doc.title)}</td>
          <td><span class="type-tag">${esc(doc.kind)}</span>${doc.pages ? ` <span class="muted">${doc.pages}p</span>` : ""}</td>
          <td>${fmt(doc.chunks)}</td>
          <td>${fmt(doc.chars)}</td>
          <td class="path" title="${esc(doc.source)}">${esc(doc.source.split("/").slice(-2).join("/"))}</td>
          <td><button class="del" data-del="${doc.id}">移除</button></td>
        </tr>`
      )
      .join("") || `<tr><td colspan="6" class="muted">还没有资料，点右上角导入。</td></tr>`;

  $$("[data-del]").forEach((button) => {
    button.onclick = async () => {
      if (!confirm("从资料库移除这篇资料？")) return;
      await fetch("/api/library/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_id: Number(button.dataset.del) }),
      });
      await loadBootstrap();
      renderLibraryPage();
      toast("已移除");
    };
  });
}

/* --------------------------------------------------------------------- eval */
async function loadEval() {
  const box = $("#eval-body");
  box.innerHTML = `<div class="empty">正在读取评测结果…</div>`;
  const data = await fetch("/api/eval").then((r) => r.json());
  if (!data.available) {
    box.innerHTML = `<div class="empty">${esc(data.hint || "还没有评测结果")}<br /><br />
      在项目根目录运行 <code>python scripts/eval.py</code> 生成。</div>`;
    return;
  }
  const m = data.metrics || {};
  const pct = (v) => (v == null ? "—" : Math.round(v * 100) + "%");
  box.innerHTML = `
    <div class="metric-grid">
      <div class="metric"><b>${pct(m.recall_at_5)}</b><span>Recall@5</span><small>正确文档进入前 5 条的比例</small></div>
      <div class="metric"><b>${pct(m.citation_hit)}</b><span>引用命中率</span><small>答案引用了正确文档的比例</small></div>
      <div class="metric"><b>${pct(m.refusal_accuracy)}</b><span>拒答准确率</span><small>无答案问题被正确拒绝</small></div>
      <div class="metric"><b>${m.p50_latency_ms ?? m.avg_latency_ms ?? "—"}<small style="display:inline">ms</small></b><span>中位响应</span><small>p50 · 均值 ${m.avg_latency_ms || "—"} ms（含模型重载）</small></div>
    </div>
    <p class="eval-summary">共 ${m.total || 0} 题（可答 ${m.answerable || 0} / 不可答 ${
    m.unanswerable || 0
  }）· 语料 ${fmt(m.corpus_chunks)} 个片段 · 生成模型 <code>${esc(data.model || "—")}</code> · 运行于 ${esc(
    data.run_at || "—"
  )}</p>
    <div class="table-wrap">
      <table class="eval-table">
        <thead><tr><th>问题</th><th>期望来源</th><th>Top-5</th><th>作答</th><th>引用</th></tr></thead>
        <tbody>
          ${(data.cases || [])
            .map(
              (c) => `<tr>
                <td>${esc(c.question)}</td>
                <td class="muted">${esc(c.expected || "—")}</td>
                <td class="${c.recall_hit ? "ok" : "no"}">${c.recall_hit ? "命中" : "未中"}</td>
                <td class="muted">${esc({llm: "模型改写", extractive: "原文摘录", insufficient: "拒答"}[c.mode] || c.mode || "—")}</td>
                <td class="${c.citation_hit ? "ok" : c.answerable ? "no" : "muted"}">${
                c.answerable ? (c.citation_hit ? "命中" : "未中") : c.mode === "insufficient" ? "已拒答" : "未拒答"
              }</td>
              </tr>`
            )
            .join("")}
        </tbody>
      </table>
    </div>`;
}

/* ------------------------------------------------------------------- events */
function switchView(view) {
  S.view = view;
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
  $$(".view").forEach((el) => (el.hidden = el.id !== `view-${view}`));
  $("#evidence-panel").style.display = view === "ask" ? "" : "none";
  document.querySelector(".layout").classList.toggle("no-evidence", view !== "ask");
  if (view === "library") renderLibraryPage();
  if (view === "eval") loadEval();
}

async function importFile(file) {
  if (!file) return;
  const status = $("#import-status");
  status.className = "import-status";
  status.textContent = `正在解析并向量化 ${file.name}……（首次调用模型会稍慢）`;
  const form = new FormData();
  form.append("file", file);
  try {
    const response = await fetch("/api/import", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "导入失败");
    status.className = "import-status ok";
    status.textContent = `已导入《${data.title}》：${data.chunks} 个片段，${fmt(data.chars)} 字符。`;
    await loadBootstrap();
    renderLibraryPage();
    setTimeout(() => $("#import-dialog").close(), 900);
  } catch (error) {
    status.className = "import-status err";
    status.textContent = error.message;
  }
}

function bind() {
  $$(".tab").forEach((tab) => (tab.onclick = () => switchView(tab.dataset.view)));
  $("#open-memory").onclick = openMemory;
  $("#save-memory").onclick = saveProfile;

  $("#open-import").onclick = () => $("#import-dialog").showModal();
  $("#open-import-2").onclick = () => $("#import-dialog").showModal();
  $("#file-input").onchange = (event) => importFile(event.target.files[0]);

  const dropzone = $("#dropzone");
  ["dragenter", "dragover"].forEach((name) =>
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add("over");
    })
  );
  ["dragleave", "drop"].forEach((name) =>
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.remove("over");
    })
  );
  dropzone.addEventListener("drop", (event) => importFile(event.dataTransfer.files[0]));

  $("#composer").onsubmit = (event) => {
    event.preventDefault();
    const value = $("#question").value;
    $("#question").value = "";
    $("#question").style.height = "auto";
    ask(value);
  };
  $("#question").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#composer").requestSubmit();
    }
  });
  $("#question").addEventListener("input", (event) => {
    const el = event.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  });

  document.addEventListener("click", (event) => {
    const askButton = event.target.closest("[data-ask]");
    if (askButton) {
      switchView("ask");
      ask(askButton.dataset.ask);
    }
  });
  $("#refresh-eval").onclick = loadEval;

  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      switchView("ask");
      $("#question").focus();
    }
    if (event.key === "Escape") {
      $$("dialog[open]").forEach((dialog) => dialog.close());
    }
  });
}

async function health() {
  try {
    const data = await fetch("/api/health").then((r) => r.json());
    $("#conn-db").classList.toggle("ok", data.db === "ok");
    $("#conn-db").classList.toggle("bad", data.db !== "ok");
    $("#conn-memory").classList.toggle("ok", data.memory === "ok");
    $("#conn-memory").classList.toggle("bad", data.memory !== "ok");
  } catch {
    $("#conn-db").classList.add("bad");
  }
}

(async function init() {
  bind();
  try {
    await loadBootstrap();
  } catch (error) {
    toast("无法连接后端：" + error.message, 5000);
  }
  health();
  setInterval(health, 30000);
})();
