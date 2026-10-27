# 验证样例

本样例对应题面「2.1 完成一次开发任务」：在一个可运行、有测试的业务工程里完成一项局部修改，
并验证上下文模块能定位规格、代码、测试与任务状态。

## 1. 任务

```text
为订单查询增加状态筛选与最低金额筛选：支持 status 与 min_total 参数，
过滤后分页，保持创建时间倒序，并校验非法参数。
```

模块收到的唯一输入是这段任务描述（`sddctx context --task ...`），没有指定文件或测试名。

## 2. 有效规格（必须出现在上下文中）

| 需求 | 位置（HEAD） | 关键约束 |
| --- | --- | --- |
| `REQ-001.1` 状态筛选 | `specs/REQ-001-order-query.md:8-12` | 合法状态 4 种；空白视为不过滤；非法值抛 `ValidationError` |
| `REQ-001.2` 金额下限 | `specs/REQ-001-order-query.md:13-17` | 单位分、**闭区间** `>=`；`None`/`0` 不过滤；负数抛错 |
| `REQ-001.3` 组合与分页 | `specs/REQ-001-order-query.md:18-22` | 状态+金额同时生效；先过滤后分页 |
| `REQ-001.4` 排序稳定 | `specs/REQ-001-order-query.md:24-27` | `created_at` 降序、同日按 `order_id` 降序 |
| `REQ-001.5` 向后兼容 | `specs/REQ-001-order-query.md:29-31` | 不传筛选参数时行为不变，且不修改仓储 |
| `REQ-002.1`–`REQ-002.3` | `specs/REQ-002-pagination.md` | 既有分页/汇总行为（回归基线） |

## 3. 初始数据

`tests/conftest.py` 固定 6 个订单（金额单位：分）：

| 订单 | 状态 | 金额 | 创建时间 |
| --- | --- | ---: | --- |
| O-1001 | paid | 5000 | 2026-10-01 10:00 |
| O-1002 | draft | 1200 | 2026-10-02 10:00 |
| O-1003 | paid | 8000 | 2026-10-03 10:00 |
| O-1004 | shipped | 5000 | 2026-10-04 10:00 |
| O-1005 | paid | 3000 | 2026-10-05 10:00 |
| O-1006 | cancelled | 9000 | 2026-10-06 10:00 |

## 4. 上下文必须包含的来源

运行 `sddctx context --project demo_project --name minimart --task "<任务>"` 后，JSON 输出中必须出现：

| 类别 | 来源 | 说明 |
| --- | --- | --- |
| 规格 | `specs/REQ-001-order-query.md:13-17`（REQ-001.2） | 闭区间与 0/负数语义 |
| 规格 | `specs/REQ-001-order-query.md:8-12`（REQ-001.1） | 状态取值与空白处理 |
| 规格 | `specs/REQ-001-order-query.md:18-22`（REQ-001.3） | 先过滤后分页 |
| 代码 | `src/minimart/service.py` → `OrderService.list_orders`、`_normalize_status`、`_normalize_min_total`、`_sorted_orders` | 待修改文件（HEAD 行号 21-95） |
| 目标测试 | `tests/test_order_filters.py` 的 9 个用例 | 每条规格至少一个用例 |
| 回归测试 | `tests/test_service_regression.py` 的 6 个用例 | 既有行为兼容 |
| 状态 | `stage=verified`、4 条 done、2 条验证结果 | 来自 PowerContext |

`context` 输出自带 `sources_verified: true`；也可单独执行
`sddctx verify --context evidence/logs/03-context.json` 复核文件与行号。

## 5. 操作步骤

```bash
cd minimax
./scripts/setup_sddctx.sh                 # ① 依赖
./scripts/pc_server.sh start              # ② PowerContext
./.venv/bin/python -m sddctx import --project demo_project --name minimart --reset
./.venv/bin/python -m sddctx context --project demo_project --name minimart \
    --task "为订单查询增加状态筛选与最低金额筛选：支持 status 与 min_total 参数，过滤后分页，保持创建时间倒序，并校验非法参数。" \
    --json --out evidence/logs/03-context.json
( cd demo_project && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ../.venv/bin/python -m pytest -q )
```

修改前执行目标测试应失败（`NotImplementedError`，`evidence/logs/before-target.log`），
修改后 15 个用例全部通过（`evidence/logs/after-*.log`）。

## 6. 预期结果

| 步骤 | 预期 |
| --- | --- |
| `import` | 10 个文档、43 个 chunk（spec 12 / code 16 / test 15），识别 10 个需求编号 |
| `context` | 3 条规格、1 个代码文件、9 个目标测试、6 个回归测试，全部来源可定位 |
| 目标测试（改动前） | 9 个全部失败，原因均为 `status/min_total filtering is not implemented` |
| 目标测试（改动后） | 9 个全部通过 |
| 回归测试 | 6 个在改动前后均通过 |
| `state set` + 重启 Server + `context` | 输出包含 4 条 done、0 条 todo、2 条验证结果 |
| 规格更新（`samples/spec-drift`） | 保留期限 30 天→90 天、版本 1→2，上下文同步变化 |

完整结果见 `evidence/logs/10-demo-summary.json`（`result: PASS`）。

## 7. 重置

```bash
./scripts/reset_environment.sh    # 删除 minimax_sdd 库与 PowerContext 本地数据
./scripts/pc_server.sh restart
```

重置后重新执行第 5 节即可复现。
