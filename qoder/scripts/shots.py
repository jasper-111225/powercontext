#!/usr/bin/env python
"""用 Marionette 驱动本机 Firefox 截图（无头）。

前置：
  HOME=$PWD/.runtime/ffhome .runtime/firefox-dl/firefox/firefox \
      --headless --marionette --no-remote about:blank
本脚本只负责连接、等待页面状态、保存 PNG。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from marionette_driver.marionette import Marionette

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "evidence" / "screens"
BASE = "http://127.0.0.1:8766"


def wait_js(m: Marionette, script: str, timeout: float = 30.0, label: str = "") -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if m.execute_script(f"return !!({script})"):
                return
        except Exception:  # noqa: BLE001 - 页面跳转期间可能短暂报错
            pass
        time.sleep(0.35)
    raise TimeoutError(f"等待超时：{label or script}")


def shot(m: Marionette, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    with path.open("wb") as fh:
        m.save_screenshot(fh)
    print(f"  saved {path.relative_to(ROOT)}  {path.stat().st_size // 1024} KB")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=2828)
    parser.add_argument("--only", default="")
    args = parser.parse_args()

    m = Marionette(host="127.0.0.1", port=args.port)
    m.start_session()
    try:
        m.set_pref("network.http.use-cache", False)
    except Exception:
        pass
    m.set_window_rect(width=1440, height=1000)
    try:
        m.navigate(f"{BASE}/?v={int(time.time())}")
        wait_js(m, "document.querySelector('#hero-chunks')?.textContent !== '0'", label="资料库加载")
        m.execute_script("window.scrollTo(0,0)")
        time.sleep(0.8)
        shot(m, "home.png")

        # 记忆弹窗
        m.execute_script("document.querySelector('#open-memory').click()")
        wait_js(m, "document.querySelector('#memory-dialog')?.open", label="记忆弹窗")
        time.sleep(0.5)
        shot(m, "memory.png")
        m.execute_script("document.querySelector('#memory-dialog').close()")

        # 资料库
        m.execute_script("document.querySelector('[data-view=\"library\"]').click()")
        wait_js(m, "document.querySelectorAll('#doc-table-body tr').length > 2", label="资料表")
        time.sleep(0.4)
        shot(m, "library.png")

        # 评测（需要 docs/evidence/eval.json）
        m.execute_script("document.querySelector('[data-view=\"eval\"]').click()")
        wait_js(m, "document.querySelectorAll('.metric').length >= 4", timeout=20, label="评测指标")
        time.sleep(0.4)
        shot(m, "eval.png")

        # 回答 + 证据
        m.execute_script("document.querySelector('[data-view=\"ask\"]').click()")
        wait_js(m, "document.querySelector('#question')", label="输入框")
        m.execute_script(
            "const q=document.querySelector('#question');"
            "q.value='Release 模式的编译产物在哪个目录？';"
            "q.dispatchEvent(new Event('input',{bubbles:true}));"
            "document.querySelector('#composer').requestSubmit();"
        )
        wait_js(
            m,
            "document.querySelectorAll('.answer-card:not(#pending)').length > 0 && document.querySelectorAll('.ev-card.used').length > 0",
            timeout=40,
            label="回答与证据",
        )
        time.sleep(0.8)
        m.execute_script("window.scrollTo(0, document.querySelector('#thread').offsetTop - 90)")
        time.sleep(0.4)
        shot(m, "answer.png")

        # 第二个问题：GDB（展示不同来源）
        m.execute_script(
            "const q=document.querySelector('#question');"
            "q.value='怎么用 GDB 调试 seekdb？';"
            "q.dispatchEvent(new Event('input',{bubbles:true}));"
            "document.querySelector('#composer').requestSubmit();"
        )
        wait_js(
            m,
            "document.querySelectorAll('.answer-card:not(#pending)').length > 1",
            timeout=40,
            label="第二个回答",
        )
        time.sleep(0.8)
        m.execute_script("window.scrollTo(0, document.querySelector('#thread').offsetTop - 90)")
        time.sleep(0.4)
        shot(m, "answer-gdb.png")
        return 0
    finally:
        m.delete_session()


if __name__ == "__main__":
    raise SystemExit(main())
