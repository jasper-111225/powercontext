# 证据目录说明

这里的文件分三类，用途不同：

| 文件 | 由谁生成 | 当前状态 |
|---|---|---|
| `logs/verify-*.md` | `scripts/verify.py` | 已包含离线自检与实时端到端验收；最新一轮 27 项全 PASS（`verify-20261028-223911.md`） |
| `logs/task-02-pytest-chunks.log` | 手工验收命令 | 已包含 `tests/test_core.py` 18 passed 与 `build-and-run.md` 6 块输出 |
| `logs/task-03-hybrid.sql` / `.out` | 手工验收命令 | seekdb 1.4 的 `SHOW CREATE TABLE chunks` 与向量/全文/RRF 检索原始输出 |
| `logs/task-03-seekdb-sql.md` | 手工记录 | 任务 03 截图的环境、查询向量来源、复现步骤与一致性核对 |
| `logs/task-06-memory-restart.md` | 手工记录 | 任务 06 验收：`pc_server.sh restart` 前后 `/api/memory` 快照一致（topic 与 33 条 entries 保留） |
| `eval.json` / `eval.md` | `scripts/eval.py` | 已收录（2026-10-28 22:52，9 篇 / 138 片段，23 题；Recall@5 95% · p50 209 ms） |
| `screens/*.png` | 人工截图 | `qoder-01.png` 至 `qoder-08.png`（07 为验收终端）、补充图 `qoder-02-output.png`、`qoder-03-output.png`、`qoder-06-home.png`、`qoder-08-output.png`，以及页面实拍 `home` / `answer` / `library` / `memory` / `eval` 与终端输出 `verify` 已收录；其余见 [`screens/README.md`](screens/README.md) |

## 关于片段数 135 与 138

`docs/验证样例.md` 与 `docs/技术说明.md` 保留了一轮已验证运行的记录，其中语料为
**138 片段 / 53,892 字符**。当前仓库里的 9 篇语料，用 `app/ingest.py` 的分块参数
（目标 760、上限 1050、重叠 110）解析得到 **135 片段 / 52,711 字符**。
两套数字来自不同的语料修订，均未手工改写。

重新运行即可得到当前环境的真实数字：

```bash
.venv/bin/python scripts/seed.py --reset
.venv/bin/python scripts/eval.py
.venv/bin/python scripts/verify.py --require-memory
```

`eval.py` 在 seekdb 或向量模型不可用时会直接退出，不会写占位结果；
`verify.py` 在服务离线时把实时检查标为 SKIP，静态检查照常执行。
