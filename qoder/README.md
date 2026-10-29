# 据典 · Judian

> **每一句答案，都能回到原文。**
>
> 一个本地优先的资料证据助手：把 Markdown / PDF / 笔记导入 seekdb，
> 用**向量 + 全文混合检索**找证据，只依据证据回答；每一句都带 `[n]`，
> 点开就是原文。学习主题、进度和偏好交给 **PowerContext** 记住。

<p align="center">
  <img src="docs/evidence/screens/home.png" alt="据典首页" width="880" />
</p>

---

## 1. 它解决什么问题

做内核、读论文、跟一个新项目时，真正的痛点往往不是「没有资料」，而是：

1. **资料散**：官方文档、PDF、笔记、聊天记录，关键词搜不全；
2. **AI 不可信**：通用聊天机器人答得很顺，但经常编，且无法核对；
3. **每次都从头开始**：上周读到哪、哪里没懂、喜欢什么详略，没人记得。

据典的三个设计原则，正好对应这三件事：

| 原则 | 实现 |
|---|---|
| **搜得全** | seekdb 同一张表里做向量近邻 + IK 全文召回，RRF 融合，两路排名可展开查看 |
| **答得可核验** | 默认直接输出原文摘录（最快、最稳）；可选开启本地模型改写，答案逐句标 `[n]`，无合法引用自动回退；证据不足直接拒答 |
| **越用越懂你** | 研究主题、回答偏好、上次问题、薄弱主题写入 PowerContext，重启后仍在 |

演示语料选了 **seekdb 官方中文开发者文档**（构建、工具链、C++ 容器库、调试、内存、日志、单测、mysqltest、编码约定），
因为「读懂一个大型 C++ 数据库内核」本身就是很好的学习场景；换成论文或团队文档只需重新导入。

---

## 2. 功能

- **导入即索引**：Markdown / TXT / HTML / PDF → 标题感知分块 → 向量化 → seekdb；PDF 保留页码。
- **混合检索**：`cosine_distance` 向量召回 + `MATCH ... AGAINST` 全文召回，RRF 融合，Top-K 可调。
- **引用式回答**：正文里的 `[n]` 可点击，右侧证据卡高亮对应片段；「查看完整原文」弹出未截断原文。
- **检索轨迹**：展开即可看到向量 Top-10、全文 Top-10、各自排名和 RRF 分数，评审可核对 seekdb 的实际作用。
- **无据拒答**：相似度与词面覆盖双阈值；材料不足时明确说「资料中没有找到足够依据」。
- **默认不依赖生成模型**：`JUDIAN_LLM_ENABLED=false`，问答 1 秒内返回；需要模型改写时设为 `true`（CPU 上会慢很多）。
- **学习记忆**：研究主题 / 本周目标 / 回答偏好 / 上次问题 / 薄弱主题；PowerContext 不可用时自动降级到本地文件，不阻塞主流程。
- **笔记**：把「问题 + 答案 + 引用」一键收进 seekdb。
- **评测页**：固定 23 题（20 可答 + 3 不可答），展示 Recall@5、引用命中率、拒答准确率、中位耗时。

---

## 3. 架构

```text
                       ┌──────────────────────────────┐
   浏览器  ───────────▶ │  FastAPI (app/main.py)        │
                       │  ├─ /api/ask   检索 + 作答     │
                       │  ├─ /api/import 上传解析       │
                       │  ├─ /api/memory 记忆读写       │
                       │  └─ /api/eval   评测结果       │
                       └───────┬──────────────┬────────┘
                               │              │
              ┌────────────────▼───┐   ┌──────▼─────────────────┐
              │ seekdb 127.0.0.1:2881│   │ PowerContext :17430    │
              │ documents / chunks   │   │ Scope: judian-learning │
              │ queries / notes      │   │ judian-profile         │
              │ VECTOR(1024) + HNSW  │   │ judian-progress        │
              │ FULLTEXT IK          │   └────────────────────────┘
              └────────┬─────────────┘
                       │ embeddings / chat
              ┌────────▼─────────────┐
              │ Ollama :11434        │
              │ qwen3-embedding:0.6b │
              │ qwen2.5:0.5b（可选） │
              └──────────────────────┘
```

**一次提问的完整链路：**

1. 问题 → `qwen3-embedding:0.6b`（1024 维）；
2. seekdb 两路召回：`ORDER BY cosine_distance(...)` 与 `MATCH(text) AGAINST(...)`；
3. RRF 融合：`score = 1/(60+rank_vec) + 1/(60+rank_ft)`，取 Top-8；
4. 置信度 = 向量相似度 × 0.75 + 词面覆盖 × 0.45（截断到 1）；
5. 生成：小模型按证据作答；无合法引用则抽取式拼句；证据不足则拒答；
6. 引用、证据、两路排名、耗时写回响应；问答日志与记忆分别落到 seekdb / PowerContext。

---

## 4. seekdb 在项目里做了什么

四张表，全部在同一个库里：

| 表 | 作用 | 关键设计 |
|---|---|---|
| `documents` | 资料元信息 | `source` 唯一键，重复导入自动覆盖 |
| `chunks` | 可引用片段 | `VECTOR(1024)` + HNSW；`FULLTEXT ... WITH PARSER ik`；`doc_id, ordinal` 索引 |
| `queries` | 每次问答 | 引用以 `JSON` 保存，便于回放 |
| `notes` | 收藏的问答 | 引用快照，原文删了笔记仍可追溯 |

两条核心 SQL（实际执行，未做抽象）：

```sql
-- 向量召回（余弦相似度）
SELECT id, 1 - cosine_distance(embedding, %s) AS vec_sim
FROM chunks ORDER BY cosine_distance(embedding, %s) LIMIT 30;

-- 全文召回（IK 中文分词）
SELECT id, MATCH(text) AGAINST(%s IN NATURAL LANGUAGE MODE) AS ft_score
FROM chunks
WHERE MATCH(text) AGAINST(%s IN NATURAL LANGUAGE MODE)
ORDER BY ft_score DESC LIMIT 30;
```

> 为什么不用纯向量？中文技术文档里有大量符号、命令、错误码（`--init`、`build_release/...`），
> 向量对这类精确串不敏感；全文恰好补上。两路 RRF 后在演示问题上明显更稳。

---

## 5. PowerContext 在项目里做了什么

- 每个学习者一个 **Scope**（`judian-learning`），首次自动创建；
- 两类显式记忆：
  - `judian-profile`：研究主题、本周目标、回答详略偏好；
  - `judian-progress`：累计提问数、上次问题、上次置信度、引用过的文档、薄弱主题；
- 每次打开页面读取，每次提问后更新；**重启 Server 后仍在**（见 `docs/验证样例.md` 的场景 5）；
- PowerContext 离线时自动写入 `.runtime/memory_fallback.json`，API 返回 `source=local`，前端如实标注。

它和 seekdb 的分工：**seekdb 记「资料与问答」，PowerContext 记「人」**。

---

## 6. 快速开始

### 6.1 前置

| 组件 | 版本 | 说明 |
|---|---|---|
| seekdb | 1.4.x（`seekdb-v1.4.0.0` 验证） | MySQL 协议，默认 `127.0.0.1:2881`，root 空密码 |
| Ollama | 0.4+ | `ollama pull qwen3-embedding:0.6b`（必需）；`ollama pull qwen2.5:0.5b`（可选） |
| Python | 3.10+ | 3.12 验证 |
| PowerContext | 1.4.0 | `uv tool install "powercontext[cli,server]==1.4.0"` |

seekdb 起法见 [部署题说明](https://github.com/oceanbase/seekdb)；如果已经按赛题一部署了 2881 实例，直接复用。

### 6.2 三条命令

```bash
cd qoder

./scripts/setup.sh          # venv + 依赖 + 建库 + 灌入 9 篇示例语料（内置向量缓存，通常 <1 分钟）
./scripts/pc_server.sh start  # 启动 PowerContext（记忆）
./scripts/run.sh            # http://127.0.0.1:8766
```

`setup.sh` 会先检查 seekdb 与向量模型，再灌库；仓库内置演示语料向量缓存，首次 clone 通常 <1 分钟；新导入的资料仍会实时调用向量模型。
换语料：把文件放进 `data/corpus/`（可在 `manifest.json` 里给中文标题），然后 `python scripts/seed.py --reset`。
Windows 下用 Git Bash 执行上述命令；脚本会自动选择 `.venv/Scripts/python.exe`。

### 6.3 最小验证

```bash
curl -s http://127.0.0.1:8766/api/health
curl -s -X POST http://127.0.0.1:8766/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"Release 模式的编译产物在哪个目录？"}'
```

期望：`mode` 为 `extractive` 或 `llm`，`citations` 非空，`evidence[*].vec_rank` / `ft_rank` 至少一路有值，
答案里包含 `build_release/src/observer/seekdb`。

---

## 7. 验证样例

完整的 6 个场景（含预期结果、实际输出、记忆重启验证、无答案拒答）见
**[docs/验证样例.md](docs/验证样例.md)**；运行证据见 **[docs/evidence/](docs/evidence/)**。

---

## 8. 评测

固定 23 题（20 可答 + 3 不可答），跑 `python scripts/eval.py` 生成：

| 指标 | 数值 |
|---|---:|
| Recall@5 | **100%**（20/20） |
| 引用命中率 | **100%**（20/20） |
| 拒答准确率 | **100%**（3/3） |
| 中位耗时 | **52 ms**（p50；均值 54 ms，`--no-llm`） |

评测页在 Web 界面「评测」标签中直接展示；`docs/evidence/eval.json` 是原始逐题结果。

---

## 9. 目录

```text
qoder/
├── app/
│   ├── main.py        # FastAPI 路由
│   ├── db.py          # seekdb schema / 双路检索 / RRF / 日志
│   ├── ingest.py      # Markdown / HTML / PDF 解析与分块
│   ├── pipeline.py    # 解析 → 向量化 → 入库（脚本与上传共用）
│   ├── embed.py       # Ollama embeddings + 磁盘缓存
│   ├── llm.py         # 生成、引用校验、抽取式兜底、拒答
│   ├── memory.py      # PowerContext 客户端 + 本地降级
│   ├── config.py      # 环境变量
│   └── static/        # 无构建步骤的前端（HTML/CSS/JS）
├── scripts/
│   ├── setup.sh       # 一键准备
│   ├── seed.py        # 灌库
│   ├── run.sh         # 启 Web
│   ├── pc_server.sh   # 启停 PowerContext
│   ├── eval.py        # 评测
│   └── verify.py      # 静态 + 端到端验收
├── data/
│   ├── corpus/        # 示例语料（seekdb 开发者文档，Apache-2.0）
│   ├── samples/       # 导入演示用的实验笔记
│   └── gold.json      # 23 题评测集
├── docs/
│   ├── 运行说明.md
│   ├── 验证样例.md
│   ├── 技术说明.md
│   ├── Qoder任务卡.md
│   ├── Qoder开发记录.md
│   ├── 提交材料.md
│   └── evidence/      # 日志、截图、eval.json
├── site/              # Qoder Sites 发布用静态站点
├── tests/             # 单元 / API / 记忆测试
├── pyproject.toml
└── .env.example
```

---

## 10. 设计取舍与边界

- **抽取式不是退路，是一等公民**：研究场景里，「原文这样写」比「模型这样说」更有用。模型只在能给出合法引用时采用。
- **小模型也能用**：0.5B 模型经常不守引用格式，所以后处理做硬校验；不通过就回到原文，不把幻觉放出去。
- **不追求通用聊天**：只回答资料库范围内的问题；资料外的问题明确拒答。
- **当前限制**：PDF 只做文本层（扫描件需 OCR）；单机单用户；向量模型 0.6B，跨语言检索一般；答案句子可能来自不同段落，读起来不如大模型顺。
- **下一步**：OCR、增量索引、按文档集合过滤、引用句子级定位、多人 Scope。

---

## 11. 来源与许可

- 示例语料来自 [oceanbase/seekdb](https://github.com/oceanbase/seekdb) 的 `docs/developer-guide/zh`（Apache-2.0），
  仅做格式转换与分块，未改内容；见 `data/corpus/seekdb-dev/SOURCES.md`。
- 代码以 Apache-2.0 发布；第三方依赖见 `pyproject.toml`。
- 本项目为 2026 OceanBase 数据库大赛生态赛道（Qoder）作品。
