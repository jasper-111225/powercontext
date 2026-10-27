# MiniMax 赛题：规有所依 —— SDD 工程上下文管理系统

面向软件持续演进的规格驱动开发（SDD）上下文模块：把**规格、代码、测试**导入 seekdb，
按开发任务检索出本次要遵守的规格条目、要改的代码符号和要跑的测试，把上下文交给
**MiniMax** 完成实际代码修改，再用 **PowerContext** 保存任务进展并在模块重启后复用。

本目录是赛题要求的交付物（`minimax/`），可独立运行，不依赖仓库其他目录。

## 1. 交付内容

| 路径 | 内容 |
| --- | --- |
| `sddctx/` | 上下文模块（CLI）：seekdb 导入 / 检索 / 上下文组装 / 状态读写 / MiniMax 客户端 |
| `demo_project/` | 业务工程：`specs/` 规格、`src/minimart/` 代码、`tests/` 测试（15 个用例） |
| `samples/spec-drift/` | 规格变更样例：改规格 → 重新导入 → 上下文跟随新内容 |
| `scripts/` | 初始化、服务启停、端到端演示、改动前后对比、MiniMax 调用脚本 |
| `evidence/` | 运行日志、MiniMax 开发记录（请求/响应原文）、代码差异、测试结果 |
| `docs/` | 运行验证样例、技术说明、开发记录、测试报告 |

## 2. 依赖与版本（实测）

| 组件 | 版本 / 方式 |
| --- | --- |
| Python | 3.12（`uv venv --python 3.12`） |
| seekdb | `seekdb-v1.4.0.0`，MySQL 协议 `127.0.0.1:2881`，用户 `root`、空密码 |
| PowerContext | `powercontext[cli,server]==1.4.0`，SQLite 存储，HTTP `127.0.0.1:17429` |
| MiniMax | `api.minimaxi.com`，OpenAI 兼容 `/v1/chat/completions`，模型 `MiniMax-M3` |
| Python 依赖 | `pymysql==1.2.3`、`pytest==9.1.1`（见 `requirements.txt`） |

> seekdb 连接可用环境变量覆盖：`SDDCTX_MYSQL_HOST/PORT/USER/PASSWORD/DATABASE`。
> PowerContext 地址/令牌：`SDDCTX_POWERCONTEXT_URL/TOKEN`。
> MiniMax：`MINIMAX_API_KEY`（或 `.runtime/minimax.key`，已被 `.gitignore` 忽略）。

## 3. 一次性初始化

```bash
cd minimax

# ① Python 环境（创建 .venv 并安装 pymysql/pytest）
./scripts/setup_sddctx.sh

# ② PowerContext CLI（只需一次；脚本会检查）
uv tool install "powercontext[cli,server]==1.4.0"

# ③ 确认 seekdb 在线（题目环境已提供；也可用自己部署的实例）
./.venv/bin/python -m sddctx doctor
```

`doctor` 的正常输出：

```json
{"seekdb": {"ok": true, "version": "5.7.25-OceanBase seekdb-v1.4.0.0", "fulltext": true},
 "powercontext": {"ok": true, "url": "http://127.0.0.1:17429"}}
```

## 4. 运行验证样例（一条命令）

```bash
cd minimax
MINIMAX_API_KEY=... ./scripts/run_demo.sh      # 重置环境 → 导入 → 上下文 → 测试 → 状态复用
```

脚本依次执行并落盘日志（`evidence/logs/`）：

1. 重置 seekdb 库与 PowerContext 数据（从空状态开始）；
2. 启动 PowerContext Server，确认任务状态为 0 条；
3. `sddctx import` 把 10 个文档切成 41 个 chunk 写入 seekdb；
4. `sddctx context` 为任务生成上下文（3 条规格 / 1 个代码文件 / 9 个目标测试 / 6 个回归测试）；
5. 运行目标测试与回归测试，记录退出码；
6. 校验上下文里每个来源都真实存在（文件 + 行号 + 内容一致）；
7. `sddctx state set` 把任务进展写入 PowerContext；
8. **重启 PowerContext Server**，`state show` 与新的 `context` 都能读到已完成/待办/验证结果；
9. 规格变更样例：改 `REQ-010.1` 的保留期限后重新导入，上下文输出从 30 天变成 90 天、版本 1→2。

最后输出 `DEMO PASS` 与 `evidence/logs/10-demo-summary.json`。

### 4.1 改动前后对比（证明代码修改有效）

```bash
cd minimax
./scripts/verify_delta.sh <改动前的 commit>
```

脚本在临时 worktree 中检出改动前版本：目标测试**失败**、回归测试**通过**；在当前版本：
目标测试**通过**、回归测试**通过**，并导出 `evidence/diff/req-001.patch`。

### 4.2 复现 MiniMax 的一次真实修改

```bash
cd minimax
export MINIMAX_API_KEY=...                  # Token Plan 的 key，不写入仓库
./.venv/bin/python scripts/minimax_apply.py \
    --context evidence/logs/03-context.json --model MiniMax-M3 --max-tokens 16000
```

脚本把上下文（规格全文、待改代码全文、目标/回归测试）发给 MiniMax，要求返回
`{"summary", "files": [{"path", "content"}]}`，校验路径后写回，并在
`evidence/minimax/` 留下 `-request.json` / `-response.json` / `-record.md`。

## 5. 上下文模块调用入口

```bash
./.venv/bin/python -m sddctx --help
```

| 命令 | 输入 | 输出 |
| --- | --- | --- |
| `import --project DIR [--name N] [--reset]` | 工程目录（`specs/`、`src/`、`tests/`） | 每个文档的 chunk 数、需求清单、seekdb 统计 |
| `context --project DIR --task "..." [--json] [--out FILE]` | 任务描述 | 适用规格、待改代码符号、目标/回归测试、任务状态（Markdown 或 JSON） |
| `state set --project DIR --stage ... --context FILE ...` | 阶段、已完成/待办、验证结果 | 写入 PowerContext 的 memory 记录 |
| `state show --project DIR [--all]` | — | 最新/全部任务状态（重启后同样可读） |
| `verify --context FILE` | 上下文 JSON | 逐条校验来源文件与行号，失败即报错 |
| `stats [--project N]` | — | seekdb 中按 kind 的 chunk/文档统计 |
| `doctor` | — | seekdb / PowerContext 连通性 |

`context` 输出的来源全部带 `路径:起止行`，并经过 `verify` 校验；规格被改动但未重新导入时，
模块会显式报 `stale index`，不会返回旧答案。

## 6. 已知限制

- 任务到规格的选择使用 seekdb 全文检索（ngram 解析器）+ 需求编号命中加分；任务描述与规格用词
  差异过大时需要重新描述任务，或先 `import` 新规格。
- 目标/回归测试的划分依赖规格文档的 `- 变更：新增/既有` 标记；未标注时退回“选中需求关联的测试”。
- MiniMax 调用受账号配额与网络影响；`--max-tokens` 需留出推理 token（实测 16k 足够）。
- PowerContext 使用 SQLite 存储，单机演示；多用户/远程部署需按其文档配置认证。
- 演示脚本会 **drop** `minimax_sdd` 库并重建，请勿指向共享实例。

## 7. 重置

```bash
./scripts/reset_environment.sh     # 删除 seekdb 库与 PowerContext 本地数据
./scripts/pc_server.sh restart     # 重新启动 PowerContext
```

## 8. 依据

- 赛题：`https://oceanbase.yuque.com/.../qlqnoryzrs7gtqbw`（仓库内 `题目/22-MiniMax.md`）
- 基线分支：`oceanbase/powercontext` → `2026-competition-ecosystem`（`d647275c`）
