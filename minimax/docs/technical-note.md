# 一页技术说明

## 1. 模块结构

```
任务文本
   │
   ▼
┌──────────────────────────── sddctx ────────────────────────────┐
│ importer.py   规格/代码/测试 → 文档 + chunk + 需求链接          │
│               specs 按标题切分；code 按 AST 符号切分；           │
│               tests 按 test_* 切分；@req 标注建立追踪边          │
│                                                                 │
│ db.py         seekdb(MySQL 协议)                                │
│               documents / chunks / chunk_reqs                   │
│               chunks.content 上建 ngram 全文索引                │
│                                                                 │
│ retriever.py  ① MATCH...AGAINST(任务) 检索规格 chunk            │
│               ② 需求编号命中加分 + “变更：新增”文档加分          │
│               ③ 用 chunk_reqs 追踪到代码符号与测试              │
│               ④ 校验来源文件/行号/内容，异常即 stale index      │
│                                                                 │
│ powercontext.py  Memory API 保存/读取任务状态（kind=sdd-task-state）│
│ minimax.py       OpenAI 兼容 chat/completions，整文件回写        │
└─────────────────────────────────────────────────────────────────┘
   │
   ▼
上下文（JSON/Markdown）：规格条目、待改代码符号、目标/回归测试、任务状态
```

## 2. 上下文选择方式

1. **检索**：把任务原文交给 seekdb 的 `MATCH(content) AGAINST(...)`，`content` 上是
   `WITH PARSER ngram` 全文索引，中文按 n-gram 命中；没有全文索引时退化为 `LIKE`。
2. **排序**：全文得分 + 5×（任务中显式出现的需求编号）+ 2×（规格文档标记为「变更：新增」）。
   取前 3 条规格 chunk，任务里点名的需求条目强制纳入。
3. **追踪**：`chunk_reqs` 把规格条目链接到代码符号（`@req` 注释）和测试（docstring `@req`）。
   - 代码：只保留与「新增需求（带小圆点编号）」有链接的符号；无则退回全部关联符号。
   - 目标测试：链接到「变更：新增」需求的用例；回归测试：其余用例。
4. **校验**：每个来源都核对 `路径 + 行号 + 文本` 与磁盘一致，不一致直接报错，
   因此规格改动但未重新导入时不会返回旧答案。
5. **状态合并**：从 PowerContext 取 `kind=sdd-task-state` 的最新 memory，附在上下文末尾。

所有 SQL 都写入 `evidence/logs/seekdb-queries.log`（含参数预览），可直接核对
「查询结果用于构造上下文」。

## 3. 三项技术的分工

| 技术 | 承担的作用 | 证据 |
| --- | --- | --- |
| MiniMax（`MiniMax-M3`，`api.minimaxi.com`） | 接收上下文并生成 `service.py` 的整文件替换，实现 REQ-001；记录请求/响应/token 用量 | `evidence/minimax/20261028-011954-*` |
| seekdb（`seekdb-v1.4.0.0`） | 存储规格/代码/测试 chunk 与需求链接，提供全文检索；查询结果决定上下文内容 | `evidence/logs/02-import.log`、`seekdb-queries.log` |
| PowerContext（1.4.0） | 保存任务阶段、done/todo、验证结果；重启 Server 后仍可读取并注入上下文 | `evidence/logs/06-state-save.log`、`07-*`、`08-*` |

## 4. 任务状态模型

```json
{
  "task_id": "sdd-8e29f257",
  "stage": "verified",
  "done": ["导入…", "生成上下文…", "用 MiniMax 完成修改", "目标/回归测试通过"],
  "todo": [],
  "verifications": [
    {"command": "pytest tests/test_order_filters.py", "exit_code": 0, "log": "evidence/logs/04-target-tests.log"},
    {"command": "pytest tests/test_service_regression.py", "exit_code": 0, "log": "evidence/logs/04-regression-tests.log"}
  ],
  "context_digest": "8e29f257…",
  "saved_at": "2026-10-28T01:22:05+08:00"
}
```

状态文本作为一条 Memory 写入（≤8 KB），读取时按 `saved_at` 取最新；模块本身无本地状态，
重启（含 PowerContext Server 重启）后仍能恢复。

## 5. 可复现性

- `scripts/reset_environment.sh` 清空 seekdb 库与 PowerContext 数据，保证从空状态开始；
- `scripts/run_demo.sh` 一条命令跑完导入 → 上下文 → 测试 → 状态 → 重启复用 → 规格变更；
- `scripts/verify_delta.sh <base>` 在临时 worktree 中证明目标测试「改动前失败、改动后通过」，
  并导出 `evidence/diff/req-001.patch`；
- 所有日志带命令、退出码与 git commit（`evidence/logs/00-versions.log`）。
