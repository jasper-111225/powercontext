# 测试报告

## 1. 环境

| 项 | 值 |
| --- | --- |
| 机器 | Linux x86_64，Python 3.12.15 |
| seekdb | `5.7.25-OceanBase seekdb-v1.4.0.0`，`127.0.0.1:2881` |
| PowerContext | 1.4.0（SQLite，`127.0.0.1:17429`） |
| pytest | 9.1.1（`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`） |
| MiniMax | `MiniMax-M3` |
| 改动前 commit | `6439d36b` |
| 改动 commit | `ec791e03` |

## 2. 命令与退出码

| 命令 | commit | 退出码 | 日志 |
| --- | --- | ---: | --- |
| `pytest tests/test_order_filters.py`（改动前） | `6439d36b` | **1** | `evidence/logs/before-target.log` |
| `pytest tests/test_service_regression.py`（改动前） | `6439d36b` | 0 | `evidence/logs/before-regression.log` |
| `pytest tests/test_order_filters.py`（改动后） | `ec791e03` | **0** | `evidence/logs/after-target.log` |
| `pytest tests/test_service_regression.py`（改动后） | `ec791e03` | 0 | `evidence/logs/after-regression.log` |

改动前的 9 个失败全部是目标行为未实现：

```text
E   NotImplementedError: status/min_total filtering is not implemented yet (REQ-001)
```

不是缺少依赖或无法启动的环境错误（`before-target.log` 可核对）。

## 3. 结果

- 目标功能测试：9 / 9 通过（改动后）
- 回归测试：6 / 6 通过（改动前后均通过）
- `scripts/verify_delta.sh 6439d36b` → `evidence/logs/delta-summary.json`：`result: PASS`
- `scripts/run_demo.sh` → `evidence/logs/10-demo-summary.json`：8 项检查全部 `true`，`result: PASS`

| 检查项 | 结果 |
| --- | --- |
| 从空状态开始（PowerContext 0 条任务记录） | ✅ |
| 目标测试通过 | ✅ |
| 回归测试通过 | ✅ |
| 上下文携带已保存状态 | ✅ |
| 状态阶段为 `verified` | ✅ |
| 重启后恢复 2 条验证结果 | ✅ |
| 规格更新后重新导入、上下文跟随新内容 | ✅ |
| 重启后状态可读 | ✅ |

## 4. 上下文与数据库核对

- `evidence/logs/02-import.log`：10 个文档 / 43 个 chunk（spec 12、code 16、test 15），
  识别 `REQ-001`、`REQ-001.1`–`REQ-001.5`、`REQ-002`、`REQ-002.1`–`REQ-002.3`。
- `evidence/logs/03-context.json`：3 条规格、1 个代码文件（4 个符号）、9 个目标测试、6 个回归测试。
- `evidence/logs/05-verify-sources.log`：所有来源文件/行号/内容校验通过。
- `evidence/logs/seekdb-queries.log`：每条 SQL 及参数，覆盖建表、导入、全文检索与追踪查询。
- `evidence/logs/06-state-save.log` / `07-state-after-restart.log`：PowerContext 写入与重启后读取。

## 5. 规格变更

`scripts/check_spec_freshness.sh`（`evidence/logs/09-spec-freshness.log`）：

| | 版本 | 规则文本 |
| --- | ---: | --- |
| 修改前 | 1 | 审计日志导出文件保留 **30 天**，… |
| 重新导入后 | 2 | 审计日志导出文件保留 **90 天**，… |

同一任务、同一数据库，规格改动并 `import` 后上下文输出随之变化，说明模块不是返回固定答案。
