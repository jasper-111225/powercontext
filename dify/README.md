# 回声酒馆 · Echo Tavern

> Dify × seekdb ｜ 2026 OceanBase 数据库大赛 · Dify 赛道（20 分）
>
> 一句话：**你说出口的秘密，会被酒馆的墙记住，然后从别人嘴里长出来。**

一个 6 轮的“以讹传讹”小游戏。你把秘密告诉酒馆里的 NPC，他会按自己的性格转述给下一个人；每传一手，传闻就被换掉一个细节。六轮之后，墙把最后听到的版本还给你，并用 **seekdb 的向量距离**算出这一局歪了多少。

- Dify 负责：对话入口、每轮的流程编排、NPC/说书人两次 LLM 调用、是否散场的条件分支。
- seekdb 负责：玩家与 NPC 信任、传闻传播链（每一代都落库）、对话记忆、**向量 + 全文双路召回**、结算时的 `cosine_distance`。

---

## 1. 怎么玩

酒馆里有三个人：

| NPC | 人设 | 转述时会怎么歪 |
|---|---|---|
| 🍷 罗莎 | 老板娘，算盘精，嘴快心软 | 往“值多少钱”上歪 |
| 🪶 阿烛 | 流浪诗人，浪漫、夸张 | 往“像一首诗”上歪 |
| 🥃 老默 | 酒保，寡言，记性极好 | 往“短、冷、准”上歪 |

一局 6 轮。每轮你说一句话，流程是：

```
你说话
  → 后端从 seekdb 取：这个 NPC 记得的事 / 他最近听来的版本 / 信任度
  → Dify 的 LLM 节点扮演 NPC：回你一句 + 给出“他会怎么转述”
  → 后端把这手转述写进 rumors 表，传给下一个人
  → 第 6 轮：算保真度、评分、传播链，说书人收尾
```

示例开局（可直接抄进对话框）：

> 罗莎，我告诉你一个秘密：城主的金库其实早就空了，里面只有一叠欠条。

之后点名换人：

> 阿烛，你听说了吗？
> 老默，你怎么看这件事？
> 罗莎，这谣言到底是谁先传出去的？

结算会给你：

```
🌙 本局结算：82 分（A）
保真度 85% · 失真度 15%
```

以及一条“第 0 代 → 第 6 代”的传播链，和一面可以打开的**回声墙**：

```
http://127.0.0.1:8765/wall/<player_id>
```

---

## 2. 目录

```
dify/
├── dsl/
│   └── echo-tavern.chatflow.yml   # 唯一需要导入 Dify 的 DSL
├── backend/                       # 配套后端（FastAPI + pymysql）
│   ├── app.py                     # HTTP API / 回声墙 HTML
│   ├── game.py                    # 选人、上下文、传播、结算
│   ├── db.py                      # seekdb 访问 + 混合检索 + RRF
│   ├── embed.py                   # OpenAI 兼容 /v1/embeddings 客户端
│   ├── personas.py                # 三位 NPC
│   ├── schema.sql                 # 5 张表（含 IK 全文索引与 HNSW 向量索引）
│   ├── requirements.txt
│   └── Dockerfile
├── scripts/
│   ├── setup.sh                   # 建 venv + 装依赖 + 初始化 seekdb
│   ├── init_db.py                 # 建表 / 灌 NPC / 连通性检查
│   ├── run_backend.sh             # 启动后端
│   ├── check_dsl.py               # 离线检查 DSL 连线、变量引用、代码节点
│   └── rehearse.py                # 没有 Dify 时，按 DSL 跑一遍（本地排练器）
├── docker-compose.echo.yaml       # 给 Dify 官方 compose 的 override
├── docker-compose.backend.yaml    # 可选：把后端也放进 Docker
├── docs/
│   ├── 验证样例.md                 # 评审照着做的 6 轮脚本 + 预期
│   ├── 运行证据.md                 # 实际跑出来的日志/截图/数据
│   └── evidence/                  # 日志与截图
└── .env.example                   # 后端环境变量样例
```

---

## 3. 运行环境

| 组件 | 版本 / 说明 |
|---|---|
| Dify | **1.18.0**（官方 Docker Compose 部署）；DSL 版本 0.3.1，Dify 1.18 可直接导入（会提示 DSL 版本较旧，可继续） |
| seekdb | **seekdb-v1.4.0.0**（`SELECT VERSION()` → `5.7.25-OceanBase seekdb-v1.4.0.0`），MySQL 协议，默认 `127.0.0.1:2881` |
| 后端 | Python 3.10+；FastAPI + pymysql |
| Embedding | 任意 OpenAI 兼容 `/v1/embeddings`；参考实现用 Ollama `qwen3-embedding:0.6b`（1024 维） |
| Chat 模型 | 任意 OpenAI 兼容 Chat 模型；参考实现用 Ollama `deepseek-r1:7b` / `qwen2.5:7b-instruct`，也可换成 DeepSeek、通义等 |

> 没有 Docker 的机器也能验证 seekdb 与玩法：见 §8「本地排练器」。

### 3.1 起 seekdb

如果已经按赛题一部署过实例（`127.0.0.1:2881`），直接复用，不要重装。检查：

```bash
mysql -h127.0.0.1 -P2881 -uroot -e "SELECT VERSION();"
# 或
obclient -h127.0.0.1 -P2881 -uroot -e "SELECT VERSION();"
```

没有实例时，按 seekdb 官方仓库的部署方式起一个 1.4.0 实例，监听 2881 即可。本项目只依赖 MySQL 协议、`VECTOR` 类型、`FULLTEXT ... WITH PARSER ik`、`cosine_distance()` 这四项能力。

### 3.2 起 embedding 服务（参考实现）

```bash
# 首次约 640 MB
ollama pull qwen3-embedding:0.6b

# 让 Dify / 后端容器能访问（本机跑后端可跳过）
OLLAMA_HOST=0.0.0.0 ollama serve
```

验证 1024 维：

```bash
curl -s http://127.0.0.1:11434/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-embedding:0.6b","input":["回声酒馆"]}' | head -c 200
```

换其他服务时，只改 `ECHO_EMBED_BASE_URL` / `ECHO_EMBED_MODEL` / `ECHO_EMBED_DIM`（维度必须和 `schema.sql` 注入的一致；改维度后要 `init_db.py --reset`）。

---

## 4. 启动后端

```bash
cd dify
cp .env.example .env          # 可选：改数据库地址、embedding 地址
bash scripts/setup.sh         # 建 .venv + 装依赖 + 建表 + 灌 NPC
bash scripts/run_backend.sh   # 0.0.0.0:8765
```

看到这些说明就绪：

```bash
curl -s http://127.0.0.1:8765/api/v1/health | python3 -m json.tool
# ok=true；seekdb.version=5.7.25-OceanBase seekdb-v1.4.0.0；embedding.dim=1024
```

后端接口（Dify 只用到前两个）：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/turn` | 取 NPC 记忆 / 信任 / 最近听来的版本，组装上下文 |
| POST | `/api/v1/commit` | 写入这一手转述、更新信任、判断是否散场、结算 |
| GET | `/api/v1/echo/{player_id}` | 传播链 + 每代保真度 |
| GET | `/wall/{player_id}` | 可直接打开的回声墙 |
| GET | `/api/v1/health` | seekdb / embedding 连通性 |
| POST | `/api/v1/reset` | 清空某个玩家（重跑样例用） |

---

## 5. 启动 Dify 并导入

### 5.1 起 Dify（官方 compose + 本项目 override）

```bash
git clone https://github.com/langgenius/dify.git
cd dify/docker
git checkout 1.18.0
cp .env.example .env
```

编辑 `.env`，让 Dify 的 HTTP 节点能访问宿主机上的后端：

```dotenv
# 允许 SSRF 代理访问 host.docker.internal（否则 HTTP 节点会 403）
SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=host.docker.internal
```

启动（第二个 `-f` 是仓库里的 override，给 api/worker 加 `host-gateway`）：

```bash
docker compose \
  -f docker-compose.yaml \
  -f /path/to/dify/docker-compose.echo.yaml \
  up -d
```

首次打开 `http://localhost/install` 建管理员账号。等 `docker compose ps` 全绿。

> macOS / Windows 的 Docker Desktop 自带 `host.docker.internal`，仍然建议保留 override（无副作用）。

### 5.2 配置模型供应商

**Settings → Model Provider → OpenAI-API-compatible → Add Model**：

| 字段 | 本地 Ollama | 云端示例（DeepSeek） |
|---|---|---|
| Base URL | `http://host.docker.internal:11434/v1` | `https://api.deepseek.com/v1` |
| API Key | `ollama`（随意填） | 你的 Key |
| Model Name | `deepseek-r1:7b` 或 `qwen2.5:7b-instruct` | `deepseek-chat` |
| Model Type | Chat | Chat |

Embedding 由后端直接调用，不需要在 Dify 里配 Embedding 模型。

### 5.3 导入 DSL

1. **Studio → Import DSL → 上传 `dsl/echo-tavern.chatflow.yml`**。
2. 打开应用 → **编排**，检查两个 LLM 节点（`NPC 即兴演出`、`说书人收尾`）的模型是否为你刚配的模型；不是就点开重选。
3. 右上角 **Publish** → **Run**。
4. 打开 `.env` 里的 `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS` 生效后，HTTP 节点应能访问 `http://host.docker.internal:8765`。

> 如果 HTTP 节点报连接失败：在 Dify 的 api 容器里执行
> `docker compose exec api curl -s http://host.docker.internal:8765/api/v1/health`。
> 通不了就检查 `extra_hosts` 与 `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS`。

---

## 6. seekdb 在应用里到底做了什么

5 张表（完整 DDL 见 [`backend/schema.sql`](backend/schema.sql)）：

| 表 | 内容 | 关键能力 |
|---|---|---|
| `players` | 玩家、当前秘密、轮次、局数 | `secret_emb VECTOR(1024)` |
| `npcs` | 三位 NPC 的人设与初始信任 | 种子数据 |
| `npc_state` | 每个玩家对每个 NPC 的信任、交谈次数 | 跨局保留 |
| `rumors` | **传播链**：generation / parent_id / speaker / listener / text / emb | `FULLTEXT ... WITH PARSER ik` + `VECTOR INDEX ... HNSW` |
| `memories` | 玩家与 NPC 说过的每一句话 | 同上 |

三个实际作用点：

1. **每轮取上下文**：`hybrid_memories()` 用两条 SQL 双路召回——向量路 `ORDER BY cosine_distance(emb, ?)`，全文路 `MATCH(text) AGAINST(?)`（IK 中文分词），再用 RRF 融合。日志里能看到 `via: vector+fulltext`。
2. **传闻传播链**：每个 NPC 只知道自己作为 `listener` 的最新版本；转述时写入新节点，`parent_id` 指向上一个版本，于是“谁把话传歪了”可以完整回放。
3. **结算失真度**：`cosine_distance(secret_emb, 最后一版 emb)` → 保真度；再和传播覆盖率、平均信任加权成评分。

Dify 自身的 Postgres 仍按官方 compose 运行；seekdb 承担的是**应用数据 + 记忆检索**，这也是题面明确允许的用途。

---

## 7. 验证

最短路径（后端已起）：

```bash
cd dify
.venv/bin/python scripts/check_dsl.py dsl/echo-tavern.chatflow.yml
curl -s http://127.0.0.1:8765/api/v1/health | python3 -m json.tool
```

完整的 6 轮操作、预期表现、如何重置见 [`docs/验证样例.md`](docs/验证样例.md)；
本机实际跑出的日志、SQL 结果、回声墙截图见 [`docs/运行证据.md`](docs/运行证据.md)。

---

## 8. 本地排练器（没有 Dify 时）

`scripts/rehearse.py` 会读同一份 DSL，按节点和连线执行 `start / http-request / code / llm / if-else / answer`，用来在没有 Docker 的机器上验证变量连线与整局流程。

```bash
# 需要后端与一个 Ollama chat 模型
.venv/bin/python scripts/rehearse.py --player demo-a --model qwen2.5:0.5b
.venv/bin/python scripts/rehearse.py --player demo-b --model deepseek-r1:7b \
  --log docs/evidence/rehearse.json
```

它**不是 Dify 的替代品**：不实现 Dify 的会话记忆、文件、工具、插件。正式体验以 Dify 导入为准；排练器只是本地证据与回归工具。

---

## 9. 常见问题

| 现象 | 处理 |
|---|---|
| `/health` 里 embedding 失败 | 确认 `ollama pull qwen3-embedding:0.6b` 完成；`ECHO_EMBED_BASE_URL` 指到能访问的 `/v1` |
| 向量维度不匹配 | `ECHO_EMBED_DIM` 与模型实际维度一致；改维度要 `init_db.py --reset` |
| Dify HTTP 节点 403 / 连不上 | `.env` 加 `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=host.docker.internal`；compose 带 override；Ollama 用 `OLLAMA_HOST=0.0.0.0` |
| 导入提示 DSL 版本较旧 | Dify 1.18 当前 DSL 0.7.0，本文件 0.3.1；选“继续导入”即可（字段与 1.18 仓库测试夹具一致） |
| NPC 说话不符合人设 | 换更强的 Chat 模型（7B 以下的小模型容易丢 JSON 或出戏）；代码节点对 JSON 有兜底 |
| 想重跑同一玩家 | `curl -X POST .../api/v1/reset -d '{"player_id":"..."}' -H 'Content-Type: application/json'` |
| 不想让 Dify 走 SSRF 代理 | 把后端部署到公网 HTTPS，改两个 HTTP 节点的 URL 即可 |

---

## 10. 提交信息

- 仓库：`https://github.com/jasper-111225/powercontext`
- 分支：`2026-competition-ecosystem`
- 目录：`dify/`
- 提交 SHA：见 [`docs/提交信息.md`](docs/提交信息.md)（推送后回填）

本作品未复用公开 DSL；NPC 人设、玩法、SQL 与后端代码均为本队编写。
