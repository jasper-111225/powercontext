# Qoder 任务卡

> 用途：在 Qoder 里完成「据典」的开发/迭代，并留下可核对的开发记录。
> 每张卡都给了**可直接粘贴的提示词**、**验收命令**和**建议截图**。
>
> 两种用法：
> - **A. 从零复现**（任务卡 01–08）：在一台干净机器上按顺序让 Qoder 实现，适合补齐「主要开发工作在 Qoder 完成」。
> - **B. 增量迭代**（任务卡 09–14）：在现有代码上继续开发，适合已经写完主体、需要真实 Qoder 记录的情况。

---

## 准备工作（在 Qoder 里）

新建一个任务，先把下面这段作为上下文发给 Qoder（或把仓库拖进 Qoder 工作区）：

```text
你在开发一个叫「据典 Judian」的本地资料证据助手。
技术栈：Python 3.12 + FastAPI + pymysql + Ollama（OpenAI 兼容接口）+ seekdb 1.4（MySQL 协议）+ PowerContext 1.4。
目录：app/（后端）、app/static/（无构建前端）、scripts/（setup/seed/run/eval/verify）、data/（语料与评测集）、docs/。
硬约束：
1) 检索必须用 seekdb 的 cosine_distance 与 MATCH ... AGAINST，不许引入 FAISS/Elasticsearch；
2) 答案必须带 [n] 引用，引用必须能落到 chunks.id；没有合法引用就改成原文摘录；
3) 资料外的问题必须拒答；
4) 记忆用 PowerContext 的 /v1/memory/remember 与 /v1/memory/entries/list，离线时降级到本地 JSON；
5) 每次改动后跑 .venv/bin/python -m pytest -q 和 scripts/verify.py。
先读 README.md、docs/技术说明.md，再开始。
```

---

## A. 从零复现（8 张卡）

### 01 · 需求拆解与数据模型

**目标**：让 Qoder 产出表结构与模块边界，而不是直接写代码。

提示词：

```text
读 README.md 与 docs/技术说明.md，输出：
1. 一次提问从 HTTP 到答案的时序（标出 seekdb / Ollama / PowerContext 的调用点）；
2. documents / chunks / queries / notes 四张表的字段、索引、为什么；
3. 三个最容易失败的点（向量维度、中文分词、引用越界）及对应测试。
不要写代码，先给设计。
```

验收：`docs/qoder/01-design.md` 存在，包含时序图与表结构；人工确认与 `app/db.py` 的 SCHEMA 一致。

截图：Qoder 输出设计的那一屏。

---

### 02 · 文档解析与分块

**目标**：实现 `app/ingest.py` 的 Markdown/PDF 分块。

提示词：

```text
实现 app/ingest.py：
- Markdown 按 #/##/### 维护标题栈，块内保留「A › B › C」标题路径；
- 目标 760 字符、上限 1050、同标题内 110 字符重叠；
- 超长段落按中文句号/问号/分号切；
- PDF 用 pypdf 按页抽文本，page 落库；HTML 去 script/style；
- 写 tests/test_core.py，覆盖：标题路径、代码围栏不被丢弃、长段落拆分。
```

验收：`.venv/bin/python -m pytest -q tests/test_core.py` 全绿；`python -c` 对 `data/corpus/seekdb-dev/build-and-run.md` 输出 6 个块。

截图：pytest 输出 + 分块结果。

---

### 03 · seekdb schema 与双路检索

**目标**：实现 `app/db.py` 的建表、写入、向量/全文召回、RRF。

提示词：

```text
实现 app/db.py：
- 建 documents/chunks/queries/notes，chunks 用 VECTOR(1024) + HNSW、FULLTEXT ... WITH PARSER ik；
- vector_search 用 1-cosine_distance 作为相似度；fulltext_search 用 MATCH ... AGAINST；
- hybrid_search 取两路各 30 条，RRF k=60 融合，返回两路原始排名与融合结果；
- 所有 SQL 参数化；写一个脚本 scripts/seed.py 灌 data/corpus。
```

验收：`python scripts/seed.py --limit 1` 能写入；`SELECT COUNT(*) FROM chunks` > 0；
对「编译产物在哪」能返回 `build-and-run.md`。

截图：seekdb SQL 客户端里的 `SHOW CREATE TABLE chunks` 与检索结果。

---

### 04 · 引用校验与拒答

**目标**：实现 `app/llm.py` 的提示词、引用清洗、抽取式兜底、拒答。

提示词：

```text
实现 app/llm.py：
- 提示词要求只依据材料、每句带 [n]、材料不足输出固定拒答句；
- 模型返回后提取 [n]，删除越界编号；一个合法引用都没有就调用 _extractive；
- _extractive 把块拆句（代码块并回「……：」说明），按词面覆盖² + 向量相似度 + RRF + 问句类型排序，3-gram 去重，取 3 句；
- 资料外问题（相似度 < 0.28 且覆盖 < 0.2）直接拒答。
写测试覆盖：无引用回退、越界编号清洗、空语料拒答。
```

验收：`pytest -q` 全绿；`curl /api/ask` 问「seekdb 支持 MongoDB 吗」返回 `insufficient`。

截图：一次正常回答（带 [1][2]）和一次拒答。

---

### 05 · Web 工作台

**目标**：三栏界面（资料库 / 问答 / 证据），引用可点击。

提示词：

```text
实现 app/static/ 的单页界面（无构建步骤）：
- 三栏：左侧资料库 + 记忆摘要 + 最近提问；中间问答流 + 输入框；右侧证据卡 + 检索轨迹；
- 答案里的 [n] 渲染成按钮，点击高亮右侧第 n 张证据卡并滚动；
- 证据卡显示 doc_title / heading / page / 向量排名 / 全文排名 / RRF；
- 「评测」标签展示 /api/eval 的指标与逐题表格；
- 适配 1440 宽与移动端；配色是纸张 + 朱红 + 墨绿。
先给我线框，确认后再写。
```

验收：打开 <http://127.0.0.1:8766>，提问、点引用、展开轨迹、导入文件都正常。

截图：首页、回答 + 证据、评测页各一张。

---

### 06 · PowerContext 记忆

**目标**：写入/读取 profile 与 progress，Server 重启后仍在。

提示词：

```text
实现 app/memory.py：
- 首次 POST /v1/scopes 创建 judian-learning，scope_id 缓存到 .runtime/powercontext_scope.json；
- POST /v1/memory/remember 写 kind=judian-profile / judian-progress；
- POST /v1/memory/entries/list 读最后一条，解析 JSON；
- Server 离线时写 .runtime/memory_fallback.json，snapshot 标记 source=local；
- /api/memory/profile 与 /api/memory 暴露给前端。
```

验收：设置研究主题 → `scripts/pc_server.sh restart` → 刷新页面，横幅仍显示主题；`docs/evidence/logs/` 留存前后快照。

截图：记忆弹窗 + 重启后首页横幅。

---

### 07 · 评测与验收脚本

**目标**：23 题评测 + 端到端 verify。

提示词：

```text
实现 scripts/eval.py 与 scripts/verify.py：
- eval 读 data/gold.json，逐题算 Recall@5、引用命中、拒答、耗时，写 docs/evidence/eval.json 与 eval.md；
- verify 调运行中的 /api/health 与 /api/ask，检查：编译产物路径、GDB 证据、资料外拒答、记忆读写；输出 PASS/FAIL 日志。
```

验收：`python scripts/eval.py` 产出指标；`python scripts/verify.py --require-memory` 全 PASS。

截图：评测页 + verify 输出。

---

### 08 · 站点与发布

**目标**：用 Qoder Sites 发布 `site/`。

提示词：

```text
把 site/ 目录发布为 Qoder Site：
- 站点名「据典 Judian」；
- 可见范围设为「任何人都可访问」；
- 发布后给我完整 URL，并保留部署记录。
```

验收：无痕窗口能打开；站点含项目简介、功能、演示截图、运行说明链接、仓库链接。

截图：Sites 发布成功页 + 无痕访问。

---

## B. 增量迭代（6 张卡，基于现有代码）

> 这些任务的产出会形成真实的新 commit，适合在已有主体上补 Qoder 记录。

### 09 · 句级引用定位

```text
现在 [n] 只能定位到片段。请实现句级定位：
- 后端在 /api/ask 的 evidence 里增加 sentences: [{text, start, end}]；
- 前端点击 [n] 时，滚动到对应句并临时高亮 1.5s；
- 保持现有 API 兼容；补测试。
```

验收：点击 [2] 时只高亮第 2 条证据里的对应句。

### 10 · 批量导入与目录监控

```text
支持 POST /api/import/batch（多文件）和 scripts/watch.py（轮询目录增量导入）：
- 相同 checksum 跳过；变更则覆盖；
- 进度事件写入 .runtime/import.log；
- 补测试。
```

### 11 · 笔记导出与复习卡

```text
把 notes 导出为 Markdown（按时间分组，含引用原文与链接），并支持生成 3 道复习问答：
- GET /api/notes/export?format=md；
- 生成内容必须只使用笔记里的原文，不得编造；
- 前端加「导出」按钮。
```

### 12 · PDF OCR 兜底

```text
PDF 文本层为空时，调用本地 tesseract（若安装）做 OCR；没有 tesseract 时给出明确提示。
不要引入云服务；补测试与运行说明。
```

### 13 · 多用户与 Scope 隔离

```text
把 JUDIAN_USER 扩展为多用户：
- 每个用户一个 PowerContext Scope、一份 notes/queries 过滤；
- 前端顶部加用户切换（demo/demo2）；
- 不允许跨用户读到记忆或笔记。
```

### 14 · 性能与缓存

```text
为 /api/ask 增加两级缓存：
- 查询向量缓存（同问题 24h）；
- 检索结果缓存（语料版本变化即失效）；
- 增加 X-Cache 响应头；压测 scripts/bench.py 输出 p50/p95。
```

---

## 记录模板

每完成一张卡，在 `docs/Qoder开发记录.md` 的表格里加一行，并保存截图到
`docs/evidence/screens/qoder-XX.png`。评审会核对：**任务卡 → 代码/测试 → 截图 → commit** 是否对得上。
