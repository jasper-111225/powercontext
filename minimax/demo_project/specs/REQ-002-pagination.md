# REQ-002 分页与汇总

- 状态：active
- 版本：1
- 变更：既有
- 负责模块：`src/minimart/service.py`（`OrderService.list_orders`、`OrderService.get_summary`）

## REQ-002.1 分页返回

`list_orders(page=1, page_size=10)` 返回按创建时间倒序排列的一页订单；不足一页时返回剩余记录，超出范围返回空列表。

## REQ-002.2 分页参数校验

`page` 必须为大于等于 1 的整数，`page_size` 必须为 1 到 100 之间的整数；否则抛出 `ValidationError`。

## REQ-002.3 汇总

`get_summary()` 返回订单总数与金额合计（分）：`{"count": int, "total_cents": int}`。

## 验收

- 回归测试：`tests/test_service_regression.py`。
