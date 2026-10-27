#!/usr/bin/env python3
"""离线自检 Dify DSL：连线、变量引用、代码节点语法与运行。

不依赖 Dify 运行时，适合提交前在本机跑一遍。真正的导入仍以 Dify 为准。
用法：python3 scripts/check_dsl.py dsl/echo-tavern.chatflow.yml
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REF = re.compile(r"\{\{#([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)#\}\}")

SYS_OUTPUTS = {
    "query",
    "user_id",
    "files",
    "conversation_id",
    "dialogue_count",
    "app_id",
    "workflow_id",
    "workflow_run_id",
}
NODE_OUTPUTS: dict[str, set[str]] = {
    "http-request": {"body", "status_code", "headers", "files"},
    "llm": {"text", "usage", "reasoning_content"},
}
SAMPLE_TURN = {
    "turn": 1,
    "turn_limit": 6,
    "phase": "playing",
    "npc": {
        "id": "rosa",
        "name": "罗莎",
        "title": "回声酒馆的老板娘",
        "persona": "精明、嘴快、心软。",
        "style": "生意经。",
        "trust": 55,
    },
    "context_text": "【酒馆】灰港，雨夜。\n【这一局】第 1/6 轮。",
    "first_secret": True,
}
SAMPLE_NPC = {
    "say": "（把杯子一推）这话可值钱。",
    "retell": "城主的金库空得能跑老鼠。",
    "trust_delta": 1,
    "mood": "curious",
}
SAMPLE_COMMIT = {
    "phase": "done",
    "turn": 6,
    "status_line": "🌙 本局结束 · 保真度 62% · 评分 71（A）",
    "finale_context": "【原始秘密】……\n【传播链】……",
    "verdict": "只歪了一点。",
    "fidelity": 0.62,
    "score": 71,
    "rank": "A",
    "timeline": [],
}


def fail(msg: str) -> None:
    print(f"✗ {msg}")
    raise SystemExit(1)


def main(path: Path) -> int:
    dsl = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("app", "kind", "version", "workflow"):
        if key not in dsl:
            fail(f"缺少顶层字段 {key}")
    if dsl["kind"] != "app":
        fail("kind 必须是 app")
    app = dsl["app"]
    if app.get("mode") != "advanced-chat":
        fail("app.mode 必须是 advanced-chat")
    if not app.get("name"):
        fail("app.name 为空")
    if not app.get("description"):
        fail("app.description 为空（导入后应用列表会显示）")

    wf = dsl["workflow"]
    graph = wf.get("graph") or {}
    nodes = {str(n["id"]): n for n in graph.get("nodes", [])}
    if not nodes:
        fail("没有节点")
    if len(nodes) != len(graph.get("nodes", [])):
        fail("存在重复节点 id")

    edges = graph.get("edges", [])
    starts = [n for n in nodes.values() if n["data"].get("type") == "start"]
    if len(starts) != 1:
        fail(f"start 节点数量应为 1，实际 {len(starts)}")

    # 连线检查
    outgoing: dict[str, list[str]] = {nid: [] for nid in nodes}
    for edge in edges:
        src, dst = str(edge.get("source")), str(edge.get("target"))
        if src not in nodes:
            fail(f"边 {edge.get('id')} 的 source 不存在：{src}")
        if dst not in nodes:
            fail(f"边 {edge.get('id')} 的 target 不存在：{dst}")
        handle = edge.get("sourceHandle")
        src_type = nodes[src]["data"].get("type")
        if src_type == "if-else":
            if handle not in ("true", "false"):
                fail(f"if-else 输出 {src} 的 sourceHandle 应为 true/false，实际 {handle!r}")
        elif handle not in (None, "source"):
            fail(f"普通节点 {src} 的 sourceHandle 应为 source，实际 {handle!r}")
        if dst in outgoing[src]:
            fail(f"重复边 {src} → {dst}")
        outgoing[src].append(dst)

    # 从 start 可达
    seen: set[str] = set()
    stack = [str(starts[0]["id"])]
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        stack.extend(outgoing.get(nid, []))
    unreachable = set(nodes) - seen
    if unreachable:
        fail(f"存在从 start 不可达的节点：{sorted(unreachable)}")

    # 每个非 answer 节点至少有一条出边
    for nid, node in nodes.items():
        if node["data"].get("type") in ("answer", "end"):
            continue
        if not outgoing.get(nid):
            fail(f"节点 {nid} 没有出边")

    # 变量引用检查
    def outputs_of(nid: str) -> set[str]:
        node = nodes[nid]
        data = node["data"]
        ntype = data.get("type")
        if nid == "sys":
            return SYS_OUTPUTS
        if ntype == "code":
            return set((data.get("outputs") or {}).keys())
        if ntype == "start":
            return {str(v.get("variable")) for v in data.get("variables", [])}
        return NODE_OUTPUTS.get(str(ntype), set())

    refs: list[tuple[str, str, str]] = []
    for nid, node in nodes.items():
        for idx, msg in enumerate(node["data"].get("prompt_template") or []):
            for m in REF.finditer(str(msg.get("text") or "")):
                refs.append((nid, m.group(1), m.group(2)))
        body = (node["data"].get("body") or {}).get("data") or []
        for item in body:
            for m in REF.finditer(str(item.get("value") or "")):
                refs.append((nid, m.group(1), m.group(2)))
        for m in REF.finditer(str(node["data"].get("answer") or "")):
            refs.append((nid, m.group(1), m.group(2)))

    for where, ref_node, ref_var in refs:
        if ref_node == "sys":
            if ref_var not in SYS_OUTPUTS:
                fail(f"{where} 引用了未知系统变量 sys.{ref_var}")
            continue
        if ref_node not in nodes:
            fail(f"{where} 引用了不存在的节点 {ref_node}.{ref_var}")
        if ref_var not in outputs_of(ref_node):
            fail(
                f"{where} 引用了 {ref_node} 未声明的输出 {ref_var}；"
                f"可用：{sorted(outputs_of(ref_node))}"
            )

    # 执行 code 节点（用样例输入）
    samples = {
        "code_turn": (SAMPLE_TURN,),
        "code_npc": (json.dumps(SAMPLE_NPC, ensure_ascii=False),),
        "code_commit": (SAMPLE_COMMIT,),
    }
    for nid, node in nodes.items():
        data = node["data"]
        if data.get("type") != "code":
            continue
        args = samples.get(nid)
        if args is None:
            fail(f"自检脚本没有为 code 节点 {nid} 准备样例输入")
        namespace: dict[str, Any] = {}
        try:
            exec(compile(str(data["code"]), f"<{nid}>", "exec"), namespace)  # noqa: S102
            result = namespace["main"](*args)
        except Exception as exc:  # noqa: BLE001
            fail(f"code 节点 {nid} 执行失败：{exc!r}")
        if not isinstance(result, dict):
            fail(f"code 节点 {nid} 的 main 必须返回 dict")
        declared = set((data.get("outputs") or {}).keys())
        if set(result) != declared:
            fail(
                f"code 节点 {nid} 返回键与 outputs 不一致："
                f"返回 {sorted(result)} vs 声明 {sorted(declared)}"
            )
        print(f"✓ code {nid}: {sorted(result)}")

    # 未使用的 code 输出（提示，不失败）
    used = {(n, v) for _, n, v in refs}
    for nid, node in nodes.items():
        if node["data"].get("type") == "code":
            for out in outputs_of(nid):
                if (nid, out) not in used:
                    print(f"! 提示：code {nid} 的输出 {out} 没有被引用")

    print(f"✓ 结构检查通过：{len(nodes)} 个节点 / {len(edges)} 条边 / {len(refs)} 处变量引用")
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "dsl/echo-tavern.chatflow.yml")
    raise SystemExit(main(target))
