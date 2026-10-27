#!/usr/bin/env python3
"""用 Dify 1.18 同款 graphon 引擎检查 DSL 是否可加载。

graphon 是 Dify 1.18 的工作流运行时（api/pyproject.toml 固定 0.8.0）。
本脚本只做 import（不执行节点），用来证明 DSL 的 kind/app.mode/节点类型/连线
能被 Dify 的引擎接受。

安装（可选，需要 Python 3.12）：
  uv venv /tmp/graphon-venv --python 3.12
  uv pip install --python /tmp/graphon-venv/bin/python \
    "graphon @ git+https://github.com/langgenius/graphon@4fa22141774dff80e90e4dfa98ea3e6fbf0421bb"
  /tmp/graphon-venv/bin/python scripts/check_with_graphon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from graphon.dsl import inspect
except ImportError:
    print("未安装 graphon；按脚本头部的命令装到独立 venv 再跑。")
    raise SystemExit(2)

path = Path(sys.argv[1] if len(sys.argv) > 1 else "dsl/echo-tavern.chatflow.yml")
plan = inspect(path.read_text(encoding="utf-8"))
print(f"file: {path}")
print(f"kind: {plan.document.kind}")
print(f"status: {plan.load_status}")
print(f"reason: {plan.load_reason}")
if plan.document.graph_config:
    print(f"nodes: {len(plan.document.graph_config.get('nodes', []))}")
    print(f"edges: {len(plan.document.graph_config.get('edges', []))}")
if str(plan.load_status).lower().endswith("loadable"):
    print("✓ Dify/graphon 认为这份 DSL 可以加载")
    raise SystemExit(0)
print("✗ 不可加载")
raise SystemExit(1)
