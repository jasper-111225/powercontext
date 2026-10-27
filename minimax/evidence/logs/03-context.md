# 开发上下文 · minimart

- 任务：为订单查询增加状态筛选与最低金额筛选：支持 status 与 min_total 参数，过滤后分页，保持创建时间倒序，并校验非法参数。
- 生成时间：2026-10-28T01:26:36+08:00
- 选择方式：seekdb full-text search over spec chunks, then REQ traceability join
- 选中需求：REQ-001.1, REQ-001.2, REQ-001.3

## 1. 适用规格（3）

### REQ-001.2 金额下限（闭区间）
- 来源：`specs/REQ-001-order-query.md:13-17`（版本 1，匹配分 7.562，需求 REQ-001.2）
  > ## REQ-001.2 金额下限（闭区间）
  >
  > `list_orders(min_total=...)` 可选参数，单位为分（整数），表示订单金额下限，比较使用闭区间（`total_cents >= min_total`）。
  > 传入 `None` 或 `0` 表示不按金额筛选。传入负数必须抛出 `ValidationError`。

### REQ-001.1 状态筛选
- 来源：`specs/REQ-001-order-query.md:8-12`（版本 1，匹配分 7.2656，需求 REQ-001.1）
  > ## REQ-001.1 状态筛选
  >
  > `list_orders(status=...)` 可选参数，用于按订单状态筛选。合法取值：`draft`、`paid`、`shipped`、`cancelled`。
  > 传入其他值必须抛出 `ValidationError`。传入 `None` 或仅含空白的字符串表示不按状态筛选。

### REQ-001.3 组合筛选与分页
- 来源：`specs/REQ-001-order-query.md:18-22`（版本 1，匹配分 7.0039，需求 REQ-001.3）
  > ## REQ-001.3 组合筛选与分页
  >
  > 状态与金额可以同时生效，结果同时满足两个条件。筛选在分页之前执行：`page` 与 `page_size` 的分页语义与
  > REQ-002.2 一致，第 1 页从筛选后的第一条记录开始。

## 2. 需要修改的代码
- `src/minimart/service.py`  需求：REQ-001.1, REQ-001.2, REQ-001.3, REQ-001.4
  - `OrderService.list_orders` 行 21-49（REQ-001.1, REQ-001.2, REQ-001.3）
  - `OrderService._sorted_orders` 行 59-65（REQ-001.4）
  - `OrderService._normalize_status` 行 67-81（REQ-001.1）
  - `OrderService._normalize_min_total` 行 83-95（REQ-001.2）

## 3. 验证测试

### 3.1 目标功能测试（9）
- `tests/test_order_filters.py::test_filter_by_status` 行 15-18（REQ-001.1）
- `tests/test_order_filters.py::test_blank_status_is_ignored` 行 21-24（REQ-001.1）
- `tests/test_order_filters.py::test_unknown_status_raises` 行 27-30（REQ-001.1）
- `tests/test_order_filters.py::test_min_total_is_inclusive` 行 33-41（REQ-001.2）
- `tests/test_order_filters.py::test_min_total_zero_is_ignored` 行 44-46（REQ-001.2）
- `tests/test_order_filters.py::test_negative_min_total_raises` 行 49-52（REQ-001.2）
- `tests/test_order_filters.py::test_filters_combine_before_pagination` 行 55-58（REQ-001.3）
- `tests/test_order_filters.py::test_filters_keep_stable_order` 行 61-64（REQ-001.4）
- `tests/test_order_filters.py::test_filters_do_not_mutate_repository` 行 67-73（REQ-001.5）

### 3.2 回归测试（6）
- `tests/test_service_regression.py::test_lists_all_orders_newest_first` 行 14-24（REQ-002.1）
- `tests/test_service_regression.py::test_pagination_returns_requested_slice` 行 27-30（REQ-002.1）
- `tests/test_service_regression.py::test_page_beyond_last_is_empty` 行 33-35（REQ-002.1）
- `tests/test_service_regression.py::test_invalid_page_raises` 行 38-41（REQ-002.2）
- `tests/test_service_regression.py::test_invalid_page_size_raises` 行 44-47（REQ-002.2）
- `tests/test_service_regression.py::test_summary_counts_and_totals` 行 50-52（REQ-002.3）

## 4. 任务状态（PowerContext）
（尚未保存任务状态）

