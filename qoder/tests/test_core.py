"""核心纯函数测试：分块、RRF、引用解析。

运行：.venv/bin/python -m pytest -q
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ingest import chunk_markdown, chunk_text  # noqa: E402
from app.llm import Answer, Answerer, _sentences, _terms  # noqa: E402

SAMPLE = """# 编译 seekdb

## 前置条件

先安装工具链，准备依赖。

## 编译

执行 `./build.sh release --init --make`。编译产物位于：

```text
build_release/src/observer/seekdb
```

支持 Release 模式，不支持 Debug 模式。
"""


def test_markdown_chunks_keep_heading_path():
    title, chunks = chunk_markdown(SAMPLE)
    assert title == "编译 seekdb"
    assert chunks
    headings = " ".join(chunk.heading for chunk in chunks)
    assert "编译 seekdb" in headings
    assert "编译" in headings


def test_sentences_merge_code_into_label():
    sentences = _sentences(SAMPLE)
    merged = [s for s in sentences if "编译产物位于" in s]
    assert merged, sentences
    assert "build_release/src/observer/seekdb" in merged[0]


def test_terms_extracts_chinese_bigrams():
    terms = _terms("首次编译前要做什么？")
    assert "编译" in terms
    assert "首次" in terms


def test_extractive_answer_has_citations():
    chunks = [
        {
            "doc_title": "编译 seekdb",
            "heading": "编译",
            "text": "执行 ./build.sh release --init。编译产物位于：build_release/src/observer/seekdb。",
            "vec_sim": 0.8,
            "rrf": 0.03,
        }
    ]
    answer = Answerer()._extractive("编译产物在哪里？", chunks, 0.8)
    assert answer.mode == "extractive"
    assert answer.citations
    assert "[1]" in answer.text


def test_llm_without_citation_falls_back(monkeypatch):
    class Fake(Answerer):
        def _call_llm(self, prompt: str) -> str:
            return "编译产物在 build_release 目录。"  # 没有 [n]

    chunks = [
        {
            "doc_title": "编译 seekdb",
            "heading": "编译",
            "text": "编译产物位于 build_release/src/observer/seekdb。",
            "vec_sim": 0.8,
            "rrf": 0.03,
        }
    ]
    answer = Fake().answer("编译产物在哪里？", chunks, style="detailed")
    assert answer.mode == "extractive"
    assert "可核验" in answer.reason


def test_llm_with_out_of_range_citation_is_cleaned(monkeypatch):
    class Fake(Answerer):
        def _call_llm(self, prompt: str) -> str:
            return "编译产物在 build_release [1]，另见 [9]。"

    chunks = [
        {
            "doc_title": "编译 seekdb",
            "heading": "编译",
            "text": "编译产物位于 build_release/src/observer/seekdb。",
            "vec_sim": 0.8,
            "rrf": 0.03,
        }
    ]
    answer = Fake().answer("编译产物在哪里？", chunks, style="detailed")
    assert answer.mode == "llm"
    assert "[9]" not in answer.text
    assert answer.citations == [1]


def test_empty_corpus_refuses():
    answer = Answerer().answer("seekdb 支持什么协议？", [])
    assert answer.mode == "insufficient"


def test_chunk_text_splits_long_paragraph():
    text = "这是一句测试。" * 400
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 1100 for chunk in chunks)
