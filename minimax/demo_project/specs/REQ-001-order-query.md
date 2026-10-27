# REQ-001 订单查询筛选

- 状态：active
- 版本：1
- 变更：新增
- 负责模块：`src/minimart/service.py`（`OrderService.list_orders`）

## REQ-001.1 状态筛选

`list_orders(status=...)` 可选参数，用于按订单状态筛选。合法取值：`draft`、`paid`、`shipped`、`cancelled`。
传入其他值必须抛出 `ValidationError`。传入 `None` 或仅含空白的字符串表示不按状态筛选。

## REQ-001.2 金额下限（闭区间）

`list_orders(min_total=...)` 可选参数，单位为分（整数），表示订单金额下限，比较使用闭区间（`total_cents >= min_total`）。
传入 `None` 或 `0` 表示不按金额筛选。传入负数必须抛出 `ValidationError`。

## REQ-001.3 组合筛选与分页

状态与金额可以同时生效，结果同时满足两个条件。筛选在分页之前执行：`page` 与 `page_size` 的分页语义与
REQ-002.2 一致，第 1 页从筛选后的第一条记录开始。

## REQ-001.4 排序稳定

筛选结果保持既有排序：`created_at` 降序；`created_at` 相同的订单按 `order_id` 降序。筛选不得打乱顺序。

## REQ-001.5 向后兼容

不传 `status` 与 `min_total` 时，`list_orders` 的行为与 REQ-002 完全一致；筛选不得修改仓储中的订单集合。

## 验收

- 功能测试：`tests/test_order_filters.py`（`@req` 标注对应条目）。
- 回归测试：`tests/test_service_regression.py`（REQ-002）。
