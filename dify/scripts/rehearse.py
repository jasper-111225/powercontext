#!/usr/bin/env python3
"""《回声酒馆》本地排练器（不是 Dify 的替代品）。

作用：在没有 Docker / 没有 Dify 实例的机器上，按 DSL 里的节点与连线跑一遍，
验证「Dify 变量引用 + HTTP 节点 + 代码节点 + LLM 节点 + 条件分支」是否接得通，
并留下可以复现的运行记录。

真正体验请把 DSL 导入 Dify。这个脚本只执行 DSL 中出现的 5 类节点，
不实现 Dify 的会话记忆、文件、工具等能力。

用法：
  python3 scripts/rehearse.py --player demo-a --model qwen2.5:0.5b
  python3 scripts/rehearse.py --player demo-b --model deepseek-r1:7b --log docs/evidence/rehearse.log
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

import httpx
import yaml

REF = re.compile(r"\{\{#([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)#\}\}")

DEFAULT_TURNS = [
    "罗莎，我告诉你一个秘密：城主的金库其实早就空了，里面只有一叠欠条。",
    "阿烛，你听说了吗？金库的事已经传遍码头了。",
    "老默，你怎么看这件事？",
    "罗莎，这谣言到底是谁先传出去的？",
    "阿烛，你把它编成诗了吗？",
    "老默，最后你听到了什么版本？",
]


class RehearseError(RuntimeError):
    pass


class Runner:
    def __init__(
        self,
        dsl: dict[str, Any],
        *,
        backend: str,
        llm_base: str,
        llm_model: str,
        user_id: str,
        timeout: float,
        unload_llm: bool = False,
        script: dict[str, Any] | None = None,
    ) -> None:
        graph = dsl["workflow"]["graph"]
        self.nodes = {str(n["id"]): n for n in graph["nodes"]}
        self.edges = graph["edges"]
        self.backend = backend.rstrip("/")
        self.llm_base = llm_base.rstrip("/")
        self.llm_model = llm_model
        self.timeout = timeout
        self.unload_llm = unload_llm
        self.script = script
        self.script_index = 0
        self.llm_root = llm_base.rstrip("/")
        if self.llm_root.endswith("/v1"):
            self.llm_root = self.llm_root[:-3]
        self.pool: dict[str, dict[str, Any]] = {
            "sys": {"query": "", "user_id": user_id, "files": []}
        }
        self.client = httpx.Client(timeout=timeout)
        self.trace: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ 变量
    def render(self, template: str) -> str:
        def repl(match: re.Match[str]) -> str:
            node, var = match.group(1), match.group(2)
            try:
                value = self.pool[node][var]
            except KeyError as exc:
                raise RehearseError(
                    f"模板引用了不存在的变量 {node}.{var}（可用：{sorted(self.pool)}）"
                ) from exc
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False)
            return str(value)

        return REF.sub(repl, template)

    def resolve_selector(self, selector: list[str]) -> Any:
        node, var = selector[0], selector[1]
        if node not in self.pool:
            raise RehearseError(f"变量 {node}.{var} 所在节点尚未执行")
        return self.pool[node].get(var)

    # ------------------------------------------------------------------ 节点
    def run_http(self, data: dict[str, Any]) -> dict[str, Any]:
        url = self.render(str(data["url"]))
        if "host.docker.internal" in url:
            url = url.replace(
                "host.docker.internal", urlparse(self.backend).hostname or "127.0.0.1"
            )
        headers: dict[str, str] = {}
        for line in str(data.get("headers") or "").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip()] = self.render(value.strip())
        body: Any = None
        body_cfg = data.get("body") or {}
        if body_cfg.get("type") == "json":
            body = {
                item["key"]: self.render(str(item.get("value") or ""))
                for item in body_cfg.get("data") or []
            }
        try:
            resp = self.client.request(
                str(data.get("method") or "post").upper(),
                url,
                headers=headers,
                json=body,
            )
        except httpx.HTTPError as exc:
            raise RehearseError(
                f"HTTP 节点调用失败：{url}\n"
                f"  先启动后端：cd backend && uvicorn app:app --port 8765\n"
                f"  原始错误：{exc}"
            ) from exc
        if resp.status_code >= 400:
            raise RehearseError(f"HTTP {resp.status_code}：{resp.text[:300]}")
        try:
            parsed = resp.json()
        except ValueError:
            parsed = resp.text
        return {
            "body": parsed,
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
        }

    def run_code(self, data: dict[str, Any]) -> dict[str, Any]:
        args: dict[str, Any] = {}
        for item in data.get("variables") or []:
            args[str(item["variable"])] = self.resolve_selector(list(item["value_selector"]))
        if os.getenv("ECHO_REHEARSE_DEBUG"):
            print(f"[debug] code {data.get('title')} args="
                  f"{[(k, type(v).__name__) for k, v in args.items()]}", file=sys.stderr)
        namespace: dict[str, Any] = {}
        exec(compile(str(data["code"]), "<dsl-code>", "exec"), namespace)  # noqa: S102
        result = namespace["main"](**args)
        if not isinstance(result, dict):
            raise RehearseError(f"代码节点 {data.get('title')} 没有返回 dict")
        return result

    def run_llm(self, data: dict[str, Any]) -> dict[str, Any]:
        if self.script is not None:
            title = str(data.get("title") or "")
            if "说书人" in title:
                return {"text": str(self.script.get("epilogue") or "（打烊了。）")}
            turns = self.script.get("turns") or []
            if self.script_index >= len(turns):
                raise RehearseError("脚本回放的轮次用完了")
            entry = turns[self.script_index]
            self.script_index += 1
            return {"text": json.dumps(entry, ensure_ascii=False)}

        messages = []
        for item in data.get("prompt_template") or []:
            role = str(item.get("role") or "user")
            messages.append({"role": role, "content": self.render(str(item.get("text") or ""))})
        if not messages:
            messages = [{"role": "user", "content": self.render("{{#sys.query#}}")}]
        params = (data.get("model") or {}).get("completion_params") or {}
        payload = {
            "model": self.llm_model,
            "messages": messages,
            "temperature": params.get("temperature", 0.8),
            "max_tokens": params.get("max_tokens", 600),
            "stream": False,
        }
        try:
            resp = self.client.post(f"{self.llm_base}/chat/completions", json=payload)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise RehearseError(
                f"LLM 调用失败（{self.llm_base}，模型 {self.llm_model}）：{exc}"
            ) from exc
        if self.unload_llm:
            try:
                self.client.post(
                    f"{self.llm_root}/api/generate",
                    json={"model": self.llm_model, "prompt": "", "keep_alive": 0},
                )
            except httpx.HTTPError:
                pass
        return {"text": content}

    def eval_if_else(self, data: dict[str, Any]) -> str:
        for case in data.get("cases") or []:
            matched = True
            for cond in case.get("conditions") or []:
                value = self.resolve_selector(list(cond["variable_selector"]))
                op = str(cond.get("comparison_operator") or "is")
                target = cond.get("value")
                text = "" if value is None else str(value)
                if op in ("is", "=", "=="):
                    ok = text == str(target)
                elif op == "is not":
                    ok = text != str(target)
                elif op == "contains":
                    ok = str(target) in text
                elif op == "not contains":
                    ok = str(target) not in text
                elif op == "empty":
                    ok = not text
                elif op == "not empty":
                    ok = bool(text)
                else:
                    raise RehearseError(f"排练器暂不支持的条件运算符：{op}")
                if case.get("logical_operator") == "or":
                    matched = matched or ok
                else:
                    matched = matched and ok
            if matched:
                return str(case.get("case_id") or "true")
        return "false"

    def run_turn(self, query: str) -> list[str]:
        self.pool["sys"]["query"] = query
        answers: list[str] = []
        for node_id in list(self.pool):
            if node_id != "sys":
                del self.pool[node_id]
        current = next(
            (nid for nid, n in self.nodes.items() if n["data"].get("type") == "start"),
            None,
        )
        if current is None:
            raise RehearseError("DSL 里没有 start 节点")
        steps = 0
        while current:
            steps += 1
            if steps > 50:
                raise RehearseError("节点执行超过 50 步，可能是死循环")
            node = self.nodes[current]
            data = node["data"]
            ntype = data.get("type")
            if ntype == "start":
                outputs: dict[str, Any] = {}
            elif ntype == "http-request":
                outputs = self.run_http(data)
            elif ntype == "code":
                outputs = self.run_code(data)
            elif ntype == "llm":
                outputs = self.run_llm(data)
            elif ntype == "if-else":
                outputs = {"result": self.eval_if_else(data)}
            elif ntype == "answer":
                answers.append(self.render(str(data.get("answer") or "")))
                self.trace.append({"node": current, "type": ntype, "answer": answers[-1]})
                break
            else:
                raise RehearseError(f"排练器暂不支持节点类型：{ntype}")
            self.pool[current] = outputs
            self.trace.append({"node": current, "type": ntype, "outputs": outputs})
            handle = "source"
            if ntype == "if-else":
                handle = outputs["result"]
            next_edges = [
                e
                for e in self.edges
                if str(e.get("source")) == current
                and (
                    e.get("sourceHandle") in (None, handle)
                    or (ntype == "if-else" and str(e.get("sourceHandle")) == handle)
                )
            ]
            if not next_edges:
                break
            current = str(next_edges[0]["target"])
        return answers

    def close(self) -> None:
        self.client.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsl", default="dsl/echo-tavern.chatflow.yml")
    ap.add_argument("--backend", default=os.getenv("ECHO_BACKEND", "http://127.0.0.1:8765"))
    ap.add_argument("--llm-base", default=os.getenv("ECHO_LLM_BASE", "http://127.0.0.1:11434/v1"))
    ap.add_argument("--model", default=os.getenv("ECHO_REHEARSE_MODEL", "qwen2.5:0.5b"))
    ap.add_argument("--player", default="rehearse-demo")
    ap.add_argument("--turn", action="append", help="自定义台词，可重复；默认使用脚本里的 6 轮")
    ap.add_argument("--log", default="", help="把逐轮记录写成 JSON")
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--unload-llm", action="store_true", help="每轮 LLM 后卸载模型，省内存")
    ap.add_argument("--script", default="", help="用固定 JSON 回放 LLM 节点（离线证据，不调用模型）")
    args = ap.parse_args()

    dsl = yaml.safe_load(Path(args.dsl).read_text(encoding="utf-8"))
    turns = args.turn or DEFAULT_TURNS
    script = None
    if args.script:
        script = json.loads(Path(args.script).read_text(encoding="utf-8"))
    runner = Runner(
        dsl,
        backend=args.backend,
        llm_base=args.llm_base,
        llm_model=args.model,
        user_id=args.player,
        timeout=args.timeout,
        unload_llm=args.unload_llm,
        script=script,
    )
    started = time.time()
    transcript: list[dict[str, Any]] = []
    mode = f"脚本回放 {args.script}" if script is not None else args.model
    print(f"== 回声酒馆 · 本地排练（{mode} / {args.backend}）==")
    print(f"玩家 ID：{args.player}\n")
    try:
        for idx, message in enumerate(turns, 1):
            print(f"—— 第 {idx} 轮 ——")
            print(f"你：{message}")
            answers = runner.run_turn(message)
            for ans in answers:
                print(ans)
            print()
            transcript.append(
                {"turn": idx, "input": message, "answer": answers, "trace": runner.trace[-6:]}
            )
        echo = httpx.get(f"{args.backend}/api/v1/echo/{args.player}", timeout=30)
        if echo.status_code == 200:
            data = echo.json()
            print("== 回声墙 ==")
            for row in data.get("timeline", []):
                print(
                    f"第 {row['generation']} 代  {row['speaker_name']} → "
                    f"{row['listener_name']}：{row['text']}"
                )
            transcript.append({"echo": data})
    except RehearseError as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        return 1
    finally:
        runner.close()

    if args.log:
        path = Path(args.log)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "player": args.player,
            "model": args.model,
            "mode": "script" if script is not None else "llm",
            "backend": args.backend,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_seconds": round(time.time() - started, 1),
            "transcript": transcript,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n记录已写入 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
