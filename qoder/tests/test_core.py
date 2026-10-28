from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.db import SeekDB
from app.ingest import MAX_CHARS, chunk_markdown, parse_document
from app.llm import (
    INSUFFICIENT_ANSWER,
    LLMError,
    _clean_citations,
    _extractive,
    answer_question,
    confidence_of,
    lexical_coverage,
    parse_citations,
)

ROOT = Path(__file__).resolve().parent.parent


def test_heading_path_keeps_section_and_drops_doc_title():
    markdown = """# 测试文档

## 第一节

这里是第一节的正文。

### 子节

子节正文。

## 第二节

第二节正文。
"""
    title, chunks = chunk_markdown(markdown, "测试文档")
    assert title == "测试文档"
    headings = [chunk.heading for chunk in chunks]
    assert "第一节" in headings
    assert "第一节 › 子节" in headings
    assert "第二节" in headings
    assert all("测试文档" not in heading for heading in headings)


def test_code_fence_is_preserved():
    markdown = """## 编译

编译产物位于：

```text
build_release/src/observer/seekdb
```
"""
    _title, chunks = chunk_markdown(markdown, "文档")
    joined = "\n".join(chunk.text for chunk in chunks)
    assert "```text" in joined
    assert "build_release/src/observer/seekdb" in joined


def test_long_paragraph_is_split_under_limit():
    markdown = "## 长文\n\n" + "这是一个用于测试超长段落拆分的句子。" * 300
    _title, chunks = chunk_markdown(markdown, "长文")
    assert len(chunks) > 1
    assert all(len(chunk.text) <= MAX_CHARS for chunk in chunks)
    assert chunks[0].text.startswith("这是一个")


def test_build_and_run_splits_into_six_chunks():
    path = ROOT / "data" / "corpus" / "seekdb-dev" / "build-and-run.md"
    document = parse_document(path, "seekdb-dev/build-and-run.md")
    assert len(document.chunks) == 6
    assert all(chunk.text for chunk in document.chunks)


def test_parse_citations_dedupes_and_rejects_out_of_range():
    valid, invalid = parse_citations("见 [1]、[9]、[1] 和 [2]", 3)
    assert valid == [1, 2]
    assert invalid == [9]


def test_clean_citations_removes_out_of_range():
    assert _clean_citations("答案 [1] 与 [9]", 3) == "答案 [1] 与"


def test_confidence_formula_is_clamped():
    assert confidence_of(0.8, 0.5) == pytest.approx(0.825)
    assert confidence_of(1.0, 1.0) == 1.0
    assert confidence_of(None, 0.0) == 0.0


def _evidence(sim: float = 0.9):
    return [
        {
            "id": 11,
            "doc_id": 1,
            "ordinal": 0,
            "heading": "编译生产二进制",
            "page": None,
            "text": "编译产物位于：\n```text\nbuild_release/src/observer/seekdb\n```",
            "doc_title": "获取代码、编译并运行 seekdb",
            "source": "seekdb-dev/build-and-run.md",
            "doc_kind": "markdown",
            "vec_sim": sim,
            "ft_score": 1.2,
            "vec_rank": 1,
            "ft_rank": 1,
            "rrf": 0.032,
        },
        {
            "id": 12,
            "doc_id": 1,
            "ordinal": 1,
            "heading": "编译 Sanity 二进制",
            "page": None,
            "text": "编译产物位于：\n```text\nbuild_sanity/src/observer/seekdb\n```",
            "doc_title": "获取代码、编译并运行 seekdb",
            "source": "seekdb-dev/build-and-run.md",
            "doc_kind": "markdown",
            "vec_sim": sim - 0.1,
            "ft_score": 0.8,
            "vec_rank": 2,
            "ft_rank": 3,
            "rrf": 0.028,
        },
    ]


class _NoLLM:
    def generate(self, system: str, user: str) -> str:
        raise AssertionError("证据不足时不应调用生成模型")


class _FixedLLM:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def generate(self, system: str, user: str) -> str:
        self.calls += 1
        return self.text


class _BrokenLLM:
    def generate(self, system: str, user: str) -> str:
        raise LLMError("offline")


def test_empty_corpus_refuses():
    settings = Settings(root=ROOT, llm_enabled=True)
    answer = answer_question(
        "Release 的产物在哪里？",
        [],
        [],
        [],
        settings=settings,
        llm=_NoLLM(),
    )
    assert answer.mode == "insufficient"
    assert answer.answer == INSUFFICIENT_ANSWER
    assert answer.citations == []


def test_low_similarity_and_coverage_refuses():
    settings = Settings(root=ROOT, llm_enabled=True)
    rows = [
        {
            "id": 1,
            "doc_id": 1,
            "ordinal": 0,
            "heading": "",
            "page": None,
            "text": "今天天气很好。",
            "doc_title": "无关",
            "source": "x.md",
            "vec_sim": 0.05,
            "ft_score": None,
            "vec_rank": 1,
            "ft_rank": None,
            "rrf": 0.01,
        }
    ]
    answer = answer_question(
        "Kubernetes Operator 怎么部署？",
        rows,
        rows,
        [],
        settings=settings,
        llm=_NoLLM(),
        corpus_contains=lambda token: False,
    )
    assert answer.mode == "insufficient"
    assert "kubernetes" in answer.reason.lower()


def test_missing_latin_entity_refuses_even_with_similar_chunks():
    settings = Settings(root=ROOT, llm_enabled=True)
    rows = _evidence()
    answer = answer_question(
        "seekdb 支持 MongoDB 协议吗？",
        rows,
        rows,
        rows,
        settings=settings,
        llm=_NoLLM(),
        corpus_contains=lambda token: token != "mongodb",
    )
    assert answer.mode == "insufficient"
    assert "mongodb" in answer.reason
    assert answer.citations == []


def test_llm_answer_with_valid_citation_is_kept():
    settings = Settings(root=ROOT, llm_enabled=True)
    rows = _evidence()
    llm = _FixedLLM("编译产物位于 build_release/src/observer/seekdb [1]。")
    answer = answer_question(
        "Release 模式的编译产物在哪个目录？",
        rows,
        rows,
        rows,
        settings=settings,
        llm=llm,
        corpus_contains=lambda token: True,
    )
    assert answer.mode == "llm"
    assert answer.citations[0]["chunk_id"] == 11
    assert "[1]" in answer.answer
    assert llm.calls == 1


def test_llm_without_valid_citation_falls_back_to_extractive():
    settings = Settings(root=ROOT, llm_enabled=True)
    rows = _evidence()
    answer = answer_question(
        "Release 模式的编译产物在哪个目录？",
        rows,
        rows,
        rows,
        settings=settings,
        llm=_FixedLLM("编译产物在 build_release/src/observer/seekdb。"),
        corpus_contains=lambda token: True,
    )
    assert answer.mode == "extractive"
    assert answer.citations
    assert "[1]" in answer.answer


def test_llm_out_of_range_citation_is_cleaned_and_falls_back():
    settings = Settings(root=ROOT, llm_enabled=True)
    rows = _evidence()
    answer = answer_question(
        "Release 模式的编译产物在哪个目录？",
        rows,
        rows,
        rows,
        settings=settings,
        llm=_FixedLLM("编译产物在这里 [9]。"),
        corpus_contains=lambda token: True,
    )
    assert answer.mode == "extractive"
    assert "[9]" not in answer.answer


def test_llm_error_falls_back_to_extractive():
    settings = Settings(root=ROOT, llm_enabled=True)
    rows = _evidence()
    answer = answer_question(
        "Release 模式的编译产物在哪个目录？",
        rows,
        rows,
        rows,
        settings=settings,
        llm=_BrokenLLM(),
        corpus_contains=lambda token: True,
    )
    assert answer.mode == "extractive"
    assert answer.llm_error
    assert answer.citations


def test_extractive_joins_code_fence_to_colon_line():
    settings = Settings(root=ROOT, llm_enabled=True)
    rows = _evidence()
    result = _extractive("Release 模式的编译产物在哪个目录？", rows, "测试")
    assert "build_release/src/observer/seekdb" in result["answer"]
    assert "[1]" in result["answer"]
    assert result["citations"][0]["chunk_id"] == 11


def test_lexical_coverage_handles_chinese_bigrams():
    coverage = lexical_coverage("Release 模式的编译产物在哪个目录？", ["编译产物位于 build_release/src/observer/seekdb"])
    assert 0 < coverage <= 1


def test_hybrid_search_rrf_merges_ranks(tmp_path, monkeypatch):
    settings = Settings(root=tmp_path)
    db = SeekDB(settings)
    vector_rows = [
        {"id": 1, "text": "a", "doc_title": "A", "heading": "", "vec_sim": 0.9},
        {"id": 2, "text": "b", "doc_title": "B", "heading": "", "vec_sim": 0.8},
    ]
    fulltext_rows = [
        {"id": 2, "text": "b", "doc_title": "B", "heading": "", "ft_score": 2.0},
        {"id": 3, "text": "c", "doc_title": "C", "heading": "", "ft_score": 1.0},
    ]
    monkeypatch.setattr(db, "vector_search", lambda *args, **kwargs: vector_rows)
    monkeypatch.setattr(db, "fulltext_search", lambda *args, **kwargs: fulltext_rows)

    result = db.hybrid_search("q", [0.1, 0.2])
    fused = result["fused"]
    assert [row["id"] for row in fused][0] == 2
    shared = next(row for row in fused if row["id"] == 2)
    assert shared["vec_rank"] == 2
    assert shared["ft_rank"] == 1
    assert abs(shared["rrf"] - (1 / 62 + 1 / 61)) < 1e-12
    assert next(row for row in fused if row["id"] == 3)["vec_sim"] is None


def test_gold_set_shape():
    gold = json.loads((ROOT / "data" / "gold.json").read_text(encoding="utf-8"))
    questions = gold["questions"]
    assert len(questions) == 23
    assert sum(1 for item in questions if item["answerable"]) == 20
    assert sum(1 for item in questions if not item["answerable"]) == 3
    assert len({item["id"] for item in questions}) == 23
    assert all(item["keywords"] for item in questions)


def test_default_settings_disable_llm():
    """默认走抽取式，避免本地 0.5B 生成把问答拖到超时。"""
    assert Settings(root=ROOT).llm_enabled is False
