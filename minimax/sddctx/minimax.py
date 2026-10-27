"""MiniMax chat-completions client and the code-modification step.

The competition requires MiniMax to take part in the actual code change. This
module sends the selected engineering context to MiniMax, receives whole-file
replacements as JSON, validates them, and writes them back. The caller records
the request/response transcript as the development record.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://api.minimaxi.com"
DEFAULT_MODEL = "MiniMax-M3"
CHAT_PATH = "/v1/chat/completions"

SYSTEM_PROMPT = """你是一名参与规格驱动开发（SDD）的工程师。
你会收到：任务描述、适用的规格条目、需要修改的代码文件全文、以及必须通过的目标测试和回归测试。
要求：
1. 只修改 src/ 下的代码文件，绝对不要修改测试或规格文件。
2. 保持规格中声明的公共接口与行为；不引入新的第三方依赖。
3. 不要删除或跳过任何测试，不要修改测试期望。
4. 输出必须是单个 JSON 对象，不要包含解释性文字，格式：
   {"summary": "一句话说明改动", "files": [{"path": "src/minimart/service.py", "content": "完整文件内容"}]}
5. content 必须是修改后的完整文件内容（不是 diff），并保留原有的中文注释与 @req 标注。"""


class MiniMaxError(RuntimeError):
    pass


class MiniMaxClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL,
                 model: str = DEFAULT_MODEL, timeout: int = 180) -> None:
        if not api_key:
            raise MiniMaxError("MiniMax API key is empty")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.2,
             max_tokens: int = 8000) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        request = urllib.request.Request(
            f"{self.base_url}{CHAT_PATH}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:800]
            raise MiniMaxError(f"MiniMax HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise MiniMaxError(f"cannot reach MiniMax at {self.base_url}: {exc.reason}") from exc

        if body.get("error"):
            error = body["error"]
            raise MiniMaxError(
                f"MiniMax error {error.get('http_code', '?')}: {error.get('message', error)}"
            )
        choices = body.get("choices") or []
        if not choices:
            raise MiniMaxError(f"MiniMax returned no choices: {str(body)[:500]}")
        message = choices[0].get("message", {})
        content = message.get("content") or message.get("reasoning_content") or ""
        return {
            "content": content,
            "model": body.get("model", self.model),
            "usage": body.get("usage", {}),
            "raw": body,
        }


def _strip_fences(text: str) -> str:
    stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    start, end = stripped.find("{"), stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    return stripped


def parse_edit_response(text: str) -> dict[str, Any]:
    candidate = _strip_fences(text)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise MiniMaxError(f"MiniMax output is not valid JSON: {exc}") from exc
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise MiniMaxError("MiniMax output has no files[]")
    for item in files:
        path = str(item.get("path", ""))
        if not path.startswith("src/") or ".." in path.split("/"):
            raise MiniMaxError(f"refusing to write outside src/: {path!r}")
        if not isinstance(item.get("content"), str) or not item["content"].strip():
            raise MiniMaxError(f"empty content for {path!r}")
    return data


def build_messages(context: dict[str, Any], project_root, extra_instructions: str = "") -> list[dict[str, str]]:
    """Compose the prompt: task + specs + full code + test sources."""
    from pathlib import Path

    root = Path(project_root)
    parts: list[str] = [f"任务：{context['task']}", ""]
    parts.append("## 适用规格")
    for spec in context["specs"]:
        reqs = ", ".join(spec["req_ids"])
        parts.append(f"### {spec['symbol']}（{spec['path']}:{spec['lines'][0]}-{spec['lines'][1]}，{reqs}）")
        parts.append(spec["excerpt"])
        parts.append("")

    parts.append("## 需要修改的代码文件（完整内容）")
    for item in context["code"]:
        path = root / item["path"]
        parts.append(f"### {item['path']}")
        parts.append("```python")
        parts.append(path.read_text(encoding="utf-8").rstrip())
        parts.append("```")
        parts.append("")

    parts.append("## 目标功能测试（必须全部通过，不要修改）")
    for test in context["tests"]["target"]:
        lines = (root / test["path"]).read_text(encoding="utf-8").splitlines()
        snippet = "\n".join(lines[test["lines"][0] - 1 : test["lines"][1]])
        parts.append(f"### {test['path']}::{test['symbol']}（{', '.join(test['req_ids'])}）")
        parts.append("```python")
        parts.append(snippet)
        parts.append("```")
        parts.append("")

    parts.append("## 回归测试（必须保持通过，不要修改）")
    for test in context["tests"]["regression"]:
        parts.append(f"- {test['path']}::{test['symbol']}")

    if extra_instructions:
        parts.append("")
        parts.append("## 补充要求")
        parts.append(extra_instructions)
    parts.append("")
    parts.append("请返回满足上述格式的 JSON。")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(parts)},
    ]


def apply_files(project_root, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from pathlib import Path

    root = Path(project_root).resolve()
    written: list[dict[str, Any]] = []
    for item in files:
        target = (root / item["path"]).resolve()
        if root not in target.parents:
            raise MiniMaxError(f"refusing to write outside project: {target}")
        before = target.read_text(encoding="utf-8") if target.exists() else None
        target.write_text(item["content"], encoding="utf-8")
        written.append(
            {
                "path": item["path"],
                "bytes": len(item["content"].encode("utf-8")),
                "changed": before != item["content"],
            }
        )
    return written
