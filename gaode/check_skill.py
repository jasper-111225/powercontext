#!/usr/bin/env python3
"""gaode/SKILL.md 结构与事实校验（纯标准库，无外部依赖）。

校验三类问题：
  1) Skill 文件本身能不能被加载：YAML 前置元数据、name/description 规范、长度；
  2) 工具名是否与官方高德 MCP Server（@amap/amap-maps-mcp-server 0.0.8）一致，
     有没有写错成 maps_search_around / maps_around 之类的「看起来对」的名字；
  3) 评分点覆盖：数字来源、时间上限、数量上限、未核实、异常说明、密钥泄漏。

用法：
    python3 gaode/check_skill.py [gaode/SKILL.md]
退出码 0 = 全过；1 = 有 FAIL。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 官方 npm 包 build/index.js 里 MAPS_TOOLS 的 12 个工具名（已核对 0.0.8）
OFFICIAL_TOOLS = {
    "maps_regeocode",
    "maps_geo",
    "maps_ip_location",
    "maps_weather",
    "maps_search_detail",
    "maps_bicycling",
    "maps_direction_walking",
    "maps_direction_driving",
    "maps_direction_transit_integrated",
    "maps_distance",
    "maps_text_search",
    "maps_around_search",
}

# 常见「想当然」的错误名，出现即判失败
WRONG_TOOL_HINTS = {
    "maps_search_around",
    "maps_around",
    "maps_walking",
    "maps_walk",
    "maps_direction_walk",
    "maps_poi_search",
    "maps_place_search",
    "maps_nearby",
    "maps_search_poi",
}

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TOOL_RE = re.compile(r"\bmaps_[a-z_]+\b")
SECRET_RE = re.compile(r"(?i)(amap[_-]?key\s*[=:]\s*[A-Za-z0-9]{8,}|key=[A-Za-z0-9]{16,})")

failures: list[str] = []
warnings: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)


def warn(cond: bool, msg: str) -> None:
    if not cond:
        warnings.append(msg)


def parse_front_matter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    block = text[4:end]
    data: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        data[key.strip()] = value
    return data


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "gaode/SKILL.md")
    check(path.exists(), f"找不到文件：{path}")
    if failures:
        return report()
    text = path.read_text(encoding="utf-8")

    # ---------- 1. 能否被加载 ----------
    fm = parse_front_matter(text)
    check(bool(fm), "缺少 YAML 前置元数据（--- name/description ---）")
    name = fm.get("name", "")
    desc = fm.get("description", "")
    check(bool(name), "frontmatter 缺少 name")
    check(bool(NAME_RE.match(name)), f"name 只允许小写字母/数字/连字符：{name!r}")
    check(len(name) <= 64, f"name 超过 64 字符：{len(name)}")
    check(len(desc) >= 40, "description 太短，无法作为触发条件")
    check(len(desc) <= 1024, f"description 超过 1024 字符：{len(desc)}")
    check("高德" in desc, "description 里应出现「高德」，便于中文任务命中")
    warn(len(text) <= 20000, f"文件 {len(text)} 字符，偏长，可能挤占运行时上下文")

    body = text[text.find("\n---", 4) + 4 :]

    # ---------- 2. 工具名 ----------
    used = set(TOOL_RE.findall(text))
    wrong = used & WRONG_TOOL_HINTS
    check(not wrong, f"使用了不存在的高德工具名：{sorted(wrong)}")
    unknown = used - OFFICIAL_TOOLS - WRONG_TOOL_HINTS
    check(not unknown, f"未知工具名（不在官方 12 个里）：{sorted(unknown)}")
    for required in ("maps_around_search", "maps_search_detail", "maps_direction_walking"):
        check(required in used, f"缺少必需工具：{required}")

    # ---------- 3. 评分点覆盖 ----------
    must_have = {
        "坐标顺序「经度,纬度」": "经度,纬度",
        "周边搜无坐标的提醒": "没有坐标",
        "时间只能来自步行接口": "只能来自",
        "最多 3 个": "不超过 3",
        "未核实标注": "未核实",
        "空结果处理": "没有搜到",
        "调用失败处理": "查询失败",
        "超时地点移出表格": "不得出现在推荐表内",
        "单轮不反问": "不要反问",
    }
    for label, needle in must_have.items():
        check(needle in body, f"缺少评分点：{label}（找不到「{needle}」）")

    # 不得把 maps_distance 当成步行时间来源
    check("maps_distance" not in body or "不是步行时间" in body,
          "提到 maps_distance 却没说明它不是步行时间，容易被误用")
    # 不得用直线距离替代
    check("直线距离" in body, "缺少「不得用直线距离替代」的红线")

    # 表格模板 6 列
    header = re.search(r"\| # \| 地点 \| 地址 \| 步行距离 \| 步行时间 \| 推荐理由 \|", body)
    check(bool(header), "回答模板缺少 6 列表头（#/地点/地址/步行距离/步行时间/推荐理由）")

    # JSON 片段必须能解析
    for snippet in re.findall(r"\{" + r"[^{}]*" + r"\}", body):
        if snippet.count(":") >= 1 and not snippet.startswith("{" + '"mcpServers"'):
            try:
                json.loads(snippet)
            except json.JSONDecodeError:
                failures.append(f"JSON 片段无法解析：{snippet[:60]}")

    # 密钥泄漏
    leaked = SECRET_RE.search(text)
    check(leaked is None, f"疑似写入了密钥：{leaked.group(0)[:32] if leaked else ''}")

    return report()


def report() -> int:
    for w in warnings:
        print(f"WARN  {w}")
    if failures:
        for f in failures:
            print(f"FAIL  {f}")
        print(f"\n结果：{len(failures)} 项失败，{len(warnings)} 项警告")
        return 1
    print(f"结果：全部通过（{len(warnings)} 项警告）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
