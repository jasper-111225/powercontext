# MiniMart 订单服务（业务工程）

供 SDD 上下文模块演示用的小型 Python 工程：无第三方运行时依赖，测试用 pytest。

```bash
python -m pytest -q        # 15 个用例（9 目标 + 6 回归）
```

- `specs/`：需求规格（`REQ-001` 订单查询筛选、`REQ-002` 分页与汇总），
  文档头部用 `- 变更：新增/既有` 区分本次任务需求与兼容需求。
- `src/minimart/`：领域模型、仓储、`OrderService`。
- `tests/`：`test_order_filters.py`（目标功能，`@req` 标注到具体需求）、
  `test_service_regression.py`（既有行为回归）。

代码里的 `# @req REQ-xxx.y`、测试 docstring 里的 `@req` 是上下文模块建立追踪关系的依据。
