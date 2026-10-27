# MiniMax 开发记录

本文件是「MiniMax 实际参与代码修改」的核对入口，原始请求/响应保存在
`evidence/minimax/`（含提示词全文与 token 用量，API Key 未落盘）。

## 1. 调用方式

| 项 | 值 |
| --- | --- |
| 端点 | `https://api.minimaxi.com/v1/chat/completions`（OpenAI 兼容） |
| 模型 | `MiniMax-M3` |
| 温度 / 上限 | `temperature=0.2`，`max_tokens=16000` |
| 调用脚本 | `scripts/minimax_apply.py`（基于 `sddctx/minimax.py`） |
| 认证 | `MINIMAX_API_KEY` 环境变量；未写入仓库或日志 |

## 2. 三次尝试（真实记录）

| # | 时间 | token 用量 | 结果 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | 2026-10-28 01:14:10 | 10041（推理 6874） | 生成并写回 `service.py` | 实现正确，但**测试用例本身的期望值写错**（O-1001 金额 5000 满足 `>=5000` 与 `>=3000`），2 个用例失败。修正测试期望、回退代码后重跑 |
| 2 | 2026-10-28 01:17:37 | 8000（推理 7999） | 未产出 JSON | 推理耗尽 `max_tokens`，content 只有 `<think>`。将上限提高到 16000 |
| 3 | 2026-10-28 01:19:54 | 6148（推理 2944） | 成功 | 15/15 测试通过（9 目标 + 6 回归），耗时 27.08 秒 |

原始文件：

- 成功：`evidence/minimax/20261028-011954-request.json` / `-response.json` / `-record.md` / `-applied.json`
- 失败：`evidence/minimax/attempts/20261028-011410-*`、`20261028-011737-*`

## 3. 提供给 MiniMax 的上下文

来自 `sddctx context`（`context_sha256=9db26d96…`，提示词 6844 字符）：

- **任务**：`为订单查询增加状态筛选与最低金额筛选：支持 status 与 min_total 参数，过滤后分页，保持创建时间倒序，并校验非法参数。`
- **规格**：`REQ-001.2`（闭区间/0/负数）、`REQ-001.1`（状态取值/空白）、`REQ-001.3`（先过滤后分页）全文
- **代码**：`src/minimart/service.py` 全文（改动前 45 行的 `OrderService`）
- **目标测试**：`tests/test_order_filters.py` 的 9 个用例源码
- **回归测试**：`tests/test_service_regression.py` 的 6 个用例名
- **约束**：只改 `src/`、不得改测试/规格、保持 `@req` 标注、返回整文件 JSON

## 4. MiniMax 的输出

改动说明（模型原文）：

> 为订单查询实现状态与最低金额参数校验、组合筛选，并确保筛选后保持创建时间倒序再分页。

写回文件：`src/minimart/service.py`（3740 字节，
sha256 `b2d149a7…` → `e73d081f…`），新增 `_normalize_status` / `_normalize_min_total`，
把 `status/min_total` 过滤放到分页之前，`min_total` 使用 `>=` 闭区间。

代码差异：`evidence/diff/req-001.patch`（`git diff 6439d36b ec791e03 -- minimax/demo_project`）。

## 5. 与提交代码的对应关系

| 项 | 值 |
| --- | --- |
| 改动前 commit | `6439d36b`（目标测试 9 个失败） |
| 应用 MiniMax 输出的 commit | `ec791e03`（目标测试 9 个通过，回归 6 个通过） |
| 改动文件 | `minimax/demo_project/src/minimart/service.py`（唯一） |

复现调用：

```bash
cd minimax
export MINIMAX_API_KEY=...
./.venv/bin/python scripts/minimax_apply.py \
    --context evidence/logs/03-context.json --model MiniMax-M3 --max-tokens 16000
```
