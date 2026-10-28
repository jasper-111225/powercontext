# Qoder 开发记录

> 项目：据典 Judian ｜ 2026 OceanBase 数据库大赛 · Qoder 生态赛道
> 开发入口：**Qoder 桌面端（Linux）**，模型 `DeepSeek-Flash 高`，项目目录 `~/qcoder`
> 账号：`jasper` ｜ UID：`01a18171-8875-7bcb-b7ff-c9637b0f1176`
> （UID 从本机 Qoder 配置读取；提交前可在 Qoder → Settings → Profile → Edit 复核，如不一致以界面为准。）

## 0. 提交清单

| 项 | 值 | 状态 |
|---|---|---|
| Qoder 账号 UID | `01a18171-8875-7bcb-b7ff-c9637b0f1176` | ✅ |
| Qoder 使用入口 | 桌面端（Linux），项目 `qoder` | ✅ |
| 会话截图 | `docs/evidence/screens/qoder-01/02/03/04/05/06/08*.png`；07 用 `verify.png` + `task-07-eval.out` | ✅ |
| 站点 URL | `https://judian-jno1o7tzhwz.qoder.website`（公开） | ✅ |
| 提交 commit | 以远端 `2026-competition-ecosystem` 最新提交为准（见 `提测记录.md`） | ✅ |
| 使用效果材料 | 暂无外部用户；不提交虚构数据 | — |

## 1. 开发方式

项目在 Qoder 桌面端中按 [`Qoder任务卡.md`](Qoder任务卡.md) 的 8 张任务卡推进：
Qoder 读取 `README.md`、`docs/技术说明.md` 与现有代码，逐项实现/重构、运行 pytest 与
`scripts/verify.py`，并把设计、SQL、记忆重启、评测、站点发布的结果留在项目里。

每次任务的关键产物：

| # | 时间 | Qoder 任务 | Qoder 做了什么 | 人工验收 | 证据 |
|---|---|---|---|---|---|
| 01 | 18:12 | 需求拆解与数据模型 | 输出 HTTP→检索→作答时序、四张表字段/索引、三个失败点；落 `docs/qoder/01-design.md` | 与 `app/db.py` 的 DDL 对照一致 | `qoder-01.png` · `docs/qoder/01-design.md` |
| 02 | 18:14–18:37 | 文档解析与分块 | 重构 `app/ingest.py`；标题路径、PDF 页码、代码围栏；补解析测试 | `pytest tests/test_core.py` 通过；6 块/1411 字符 | `qoder-02.png` · `qoder-02-output.png` · `task-02-pytest-chunks.log` |
| 03 | 18:16–19:02 | seekdb schema 与双路检索 | 实现 `SeekDB`、向量/全文/标题加权/RRF；记录真实 SQL | DDL 与 SQL 落盘；检索命中构建文档 | `qoder-03.png` · `qoder-03-output.png` · `task-03-*.md/out` |
| 04 | 18:19 | 引用校验与拒答 | 审阅并补齐 `app/llm.py`：合法 `[n]`、越界清洗、抽取式兜底、OOV 拒答 | 相关 8 项测试通过；全量 36 项通过（任务 04 当时 34 项，后续补 2 项） | `qoder-04.png` |
| 05 | 18:28 | Web 工作台 | 三栏页面、引用点击高亮、检索轨迹、资料库、评测页；`styles.css` + 模板 | 浏览器实测提问/导入/轨迹正常 | `qoder-05.png` |
| 06 | 22:30 | PowerContext 记忆 | 写入/读回 profile 与 progress；重启 Server 后复读；离线降级 | `verify.py` 27 项全 PASS；主题恢复 | `qoder-06.png` · `qoder-06-home.png` · `task-06-memory-restart.md` |
| 07 | 20:37 | 评测与验收脚本 | 生成 23 题评测与 27 项验收；记录 p50/均值与逐题结果 | `eval.py` 与 `verify.py` 可复跑 | `verify.png` · `task-07-eval.out` · `eval.json` |
| 08 | 22:28 | 站点与发布 | 把 `site/` 构建为静态站并发布到 Qoder Sites，设为公开 | 无痕窗口可打开；站点含简介/演示/指标/运行说明 | `qoder-08.png` · `qoder-08-output.png` · `site/.据典 Judian.qoder.site` |

> Qoder 工作区本身没有 git 历史；以上改动统一落在本仓库的一次提交中，
> 会话截图的时间与文件修改时间一一对应。未发生的会话、未跑过的命令不写入本表。

## 2. 可核对的技术证据（不依赖 Qoder）

| 证据 | 位置 |
|---|---|
| 单元测试 | `tests/test_core.py`、`test_api.py`、`test_memory.py`；`pytest -q` 36 项 |
| 端到端验收 | `scripts/verify.py`；`docs/evidence/logs/verify-20261028-223911.md`（27 PASS） |
| 评测逐题结果 | `docs/evidence/eval.json`、`eval.md`（Recall@5 95% · 引用 95% · 拒答 100% · p50 209 ms） |
| seekdb 实际 SQL | `app/db.py`；`docs/evidence/logs/task-03-seekdb-sql.md` |
| PowerContext 读写 | `app/memory.py`；`docs/evidence/logs/task-06-memory-restart.md` |
| 页面截图 | `docs/evidence/screens/` |

## 3. 提交前自检

```bash
# 1. 代码与测试
.venv/bin/python -m pytest -q                 # 36 passed
JUDIAN_BASE=http://127.0.0.1:8766 .venv/bin/python scripts/verify.py   # 27 PASS

# 2. 站点无痕访问
#    浏览器无痕窗口打开 https://judian-jno1o7tzhwz.qoder.website

# 3. 记录齐全
ls docs/evidence/screens/qoder-*.png
```

> 平台字段与材料映射见 [`提交材料.md`](提交材料.md)。
