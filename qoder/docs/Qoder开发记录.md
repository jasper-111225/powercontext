# Qoder 开发记录

> **状态（2026-10-28）**：项目代码、语料、评测、站点素材已完成并可在本机复跑。
> **Qoder 会话记录尚待账号持有人执行 `docs/Qoder任务卡.md` 后补齐**——本文件不预填任何未发生的会话、
> 不伪造截图或 UID。提交前请完成下面的「待补清单」，否则该赛道的基础验收第 3 条（Qoder 开发可核对）不成立。

## 0. 待补清单

| 项 | 值 | 状态 |
|---|---|---|
| Qoder 账号 UID | `待填`（Qoder → Settings → Profile → Edit） | ☐ |
| Qoder 使用入口 | 桌面端 / IDE / CLI（删去不用的） | ☐ |
| 会话截图 | `docs/evidence/screens/qoder-01.png` … `qoder-08.png` | ☐ |
| 站点 URL | `待填`（Qoder Sites 发布后） | ☐ |
| 提交 commit SHA | `待填`（40 位） | ☐ |
| 使用效果材料（可选） | 访问统计 / 问卷 / 使用日志 | ☐ |

## 1. 开发方式

本项目按 **8 张任务卡**组织开发，每张卡都有目标、可直接粘贴的提示词、验收命令和截图点，
见 [`Qoder任务卡.md`](Qoder任务卡.md)。建议在 Qoder 中：

1. 新建任务，先把「准备工作」段落作为上下文；
2. 依次执行 01–08，每张卡完成后立刻跑验收命令，通过后提交一个 commit；
3. 每张卡保存 1 张截图到 `docs/evidence/screens/qoder-XX.png`；
4. 在本文件 §3 的表格里补一行（时间、任务、Qoder 产出、人工验收、commit）。

> 若主体代码已在其他环境完成，可改用任务卡 09–14 做增量迭代；但「主要开发工作」仍需在 Qoder 中发生，
> 否则不满足题面 2.1。

## 2. 任务卡与代码的对应关系

| 任务卡 | 主要产物 | 验收 |
|---|---|---|
| 01 需求与数据模型 | `docs/技术说明.md` §1、`app/db.py` SCHEMA | 表结构一致 |
| 02 解析与分块 | `app/ingest.py`、`tests/test_core.py` | `pytest -q` |
| 03 seekdb 双路检索 | `app/db.py`、`scripts/seed.py` | 检索命中 `build-and-run.md` |
| 04 引用与拒答 | `app/llm.py`、测试 | 无引用回退 / 资料外拒答 |
| 05 Web 工作台 | `app/static/` | 提问、点引用、轨迹、导入 |
| 06 记忆 | `app/memory.py`、`scripts/pc_server.sh` | 重启后记忆仍在 |
| 07 评测 | `scripts/eval.py`、`scripts/verify.py` | `eval.json` + verify 全 PASS |
| 08 站点 | `site/`、Qoder Sites | 无痕可访问 |

## 3. 会话记录（待补）

| # | 时间 | Qoder 任务 | Qoder 做了什么 | 人工验收 | commit | 截图 |
|---|---|---|---|---|---|---|
| 01 |  | 需求拆解与数据模型 |  |  |  | `qoder-01.png` |
| 02 |  | 文档解析与分块 |  |  |  | `qoder-02.png` |
| 03 |  | seekdb schema 与双路检索 |  |  |  | `qoder-03.png` |
| 04 |  | 引用校验与拒答 |  |  |  | `qoder-04.png` |
| 05 |  | Web 工作台 |  |  |  | `qoder-05.png` |
| 06 |  | PowerContext 记忆 |  |  |  | `qoder-06.png` |
| 07 |  | 评测与验收脚本 |  |  |  | `qoder-07.png` |
| 08 |  | 站点与发布 |  |  |  | `qoder-08.png` |

## 4. 可核对的技术证据（不依赖 Qoder）

| 证据 | 位置 |
|---|---|
| 单元测试 | `tests/test_core.py`；`pytest -q` |
| 端到端验收 | `scripts/verify.py`；`docs/evidence/logs/verify-*.md` |
| 评测逐题结果 | `docs/evidence/eval.json`、`eval.md` |
| seekdb 实际 SQL | `app/db.py`；`docs/技术说明.md` §2 |
| PowerContext 读写 | `app/memory.py`；记忆重启验证见 `docs/验证样例.md` 场景 5 |
| 页面截图 | `docs/evidence/screens/` |

## 5. 提交前自检

```bash
# 1. 代码与测试
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify.py --require-memory

# 2. 站点无痕访问
#    用浏览器无痕窗口打开 Qoder Sites URL，确认未登录可访问

# 3. 记录齐全
ls docs/evidence/screens/qoder-*.png   # 8 张
#    本文件 §0 的 6 项全部打勾
```

> 提交材料与平台字段见 [`提交材料.md`](提交材料.md)。
