"""回答生成：严格基于检索到的材料，输出可核验的 [n] 引用。

小模型（qwen2.5:0.5b）在严格格式上并不可靠，所以这里有两道保险：
  1. 提示词要求「只依据材料」并给出编号；
  2. 后处理校验引用；一旦模型没给出合法引用，就退化为**抽取式回答**——
     直接从原文挑句子并标注来源。这样任何环境下都不会出现「无出处的答案」。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import settings

REFUSAL = "资料中没有找到足够依据来回答这个问题。换一种问法，或把这个主题的资料导入资料库后再试。"
_SENTENCE = re.compile(r"(?<=[。！？!?；;])")
_CITATION = re.compile(r"\[(\d{1,2})\]")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]{2,}")
_GENERIC = {"seekdb", "oceanbase", "sql", "api", "http", "https", "json"}
_STOP = {
    "的", "了", "是", "在", "和", "与", "及", "或", "有", "吗", "呢", "怎么", "如何",
    "什么", "哪些", "为什么", "可以", "需要", "一个", "这个", "那个", "seekdb",
}


def _terms(text: str) -> set[str]:
    text = text.lower()
    terms: set[str] = set(re.findall(r"[a-z0-9_./-]{2,}", text))
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk:
        if len(run) == 1:
            continue
        for i in range(len(run) - 1):
            bigram = run[i : i + 2]
            if bigram not in _STOP:
                terms.add(bigram)
        if len(run) <= 6:
            terms.add(run)
    return {t for t in terms if t not in _STOP}


def _overlap(query_terms: set[str], text: str) -> float:
    if not query_terms:
        return 0.0
    low = text.lower()
    hits = sum(1 for term in query_terms if term in low)
    return hits / len(query_terms)


def _clean_sentence(sentence: str) -> str:
    sentence = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", sentence)
    sentence = re.sub(r"[`*]{1,3}", "", sentence)
    sentence = sentence.strip(" \t\r\n->#|")
    sentence = re.sub(r"\s+", " ", sentence)
    return sentence


def _sentences(text: str) -> list[str]:
    """把块拆成候选句子；代码块并进相邻的「……：」说明，列表项去掉符号。"""
    parts = re.split(r"```[^\n]*\n(.*?)```", text, flags=re.S)
    out: list[str] = []
    for index, part in enumerate(parts):
        if index % 2 == 1:  # 代码块
            code = " ".join(line.strip() for line in part.splitlines() if line.strip())
            if not code:
                continue
            if out and out[-1].rstrip().endswith(("：", ":")):
                out[-1] = out[-1].rstrip() + " " + code
            else:
                out.append(code)
            continue
        for block in re.split(r"\n{2,}", part):
            block = block.strip()
            if not block or block.startswith("|") or set(block) <= set("-|: "):
                continue
            for sentence in _SENTENCE.split(block):
                cleaned = _clean_sentence(sentence)
                if cleaned:
                    out.append(cleaned)
    return [item for item in out if len(item) >= 12 or item.rstrip().endswith(("：", ":"))]


def _intent_bonus(question: str, sentence: str) -> float:
    """按问句类型给句式加权：问位置就看路径，问步骤就看命令。"""
    bonus = 0.0
    if re.search(r"哪里|在哪|位置|路径|目录|地址|哪个文件", question):
        if re.search(r"/|\b[a-z_]+\.[a-z]+\b|build_|tools/", sentence):
            bonus += 1.4
    if re.search(r"怎么|如何|步骤|怎样", question):
        if re.search(r"执行|运行|使用|通过|命令|安装|配置|添加", sentence):
            bonus += 0.8
    if re.search(r"为什么|原因|区别|对比", question):
        if re.search(r"因为|由于|原因|区别|相比|而", sentence):
            bonus += 0.8
    return bonus


def _similar(a: str, b: str) -> float:
    """3-gram Jaccard，用于去掉重叠切块带来的近似重复句。"""
    def grams(value: str) -> set[str]:
        value = re.sub(r"\s+", "", value)
        return {value[i : i + 3] for i in range(max(0, len(value) - 2))}

    A, B = grams(a), grams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def _unknown_entities(
    question: str, chunks: list[dict[str, Any]], corpus_text: str | None = None
) -> list[str]:
    """问句里出现了资料里完全没有的英文实体（mongodb / gpu / k8s…），直接判无依据。

    中文问句里混英文专有名词很常见；这类词一旦 OOV，往往意味着问题超出语料，
    比「向量相似度低」更可靠。
    """
    latin = {token.lower() for token in _LATIN.findall(question)}
    latin -= _GENERIC
    if not latin:
        return []
    corpus = (corpus_text or " ".join(chunk.get("text", "") for chunk in chunks)).lower()
    return sorted(token for token in latin if token not in corpus)


def _label(index: int, chunk: dict[str, Any]) -> str:
    parts = [f"[{index}] 《{chunk.get('doc_title') or '资料'}》"]
    if chunk.get("heading"):
        parts.append(f"› {chunk['heading']}")
    if chunk.get("page"):
        parts.append(f"（第 {chunk['page']} 页）")
    return " ".join(parts)


@dataclass
class Answer:
    text: str
    mode: str  # llm | extractive | insufficient
    reason: str = ""
    citations: list[int] = field(default_factory=list)


class Answerer:
    def __init__(self, s=settings) -> None:
        self.s = s

    # ------------------------------------------------------------------ llm
    def _call_llm(self, prompt: str) -> str:
        payload = {
            "model": self.s.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是「据典」，一个只依据给定材料回答的中文研究助手。"
                    "你不编造事实，每个结论后面都标注材料编号，例如 [1]。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": self.s.llm_max_tokens,
            "stream": False,
        }
        with httpx.Client(timeout=self.s.llm_timeout) as client:
            response = client.post(f"{self.s.llm_base}/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
        return (data["choices"][0]["message"]["content"] or "").strip()

    def _prompt(self, question: str, chunks: list[dict[str, Any]], style: str) -> str:
        blocks = []
        for i, chunk in enumerate(chunks, start=1):
            text = chunk["text"].strip()
            if len(text) > 1100:
                text = text[:1100] + "……"
            blocks.append(f"{_label(i, chunk)}\n{text}")
        length = "3 到 5 句" if style != "detailed" else "6 到 8 句，可以分点"
        return (
            "请只根据下面的材料回答问题。\n"
            f"要求：用中文，{length}；每个事实句末尾标注来源编号，如 [1] 或 [1][2]；"
            "不要引用材料之外的编号；如果材料不足以回答，只输出「资料中没有找到足够依据」。\n\n"
            "材料：\n\n" + "\n\n".join(blocks) + f"\n\n问题：{question}\n\n回答："
        )

    # ------------------------------------------------------------ extractive
    def _extractive(
        self, question: str, chunks: list[dict[str, Any]], top_sim: float
    ) -> Answer:
        """从原文里挑句子。默认路径：不依赖生成模型，句句可核验。"""
        terms = _terms(question)
        top_doc = chunks[0].get("doc_id") if chunks else None
        wants_location = bool(re.search(r"哪里|在哪|位置|路径|目录|地址|哪个文件", question))
        candidates: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks, start=1):
            for position, sentence in enumerate(_sentences(chunk["text"])):
                if len(sentence) < 8:
                    continue
                if len(sentence) > 240:
                    sentence = sentence[:240] + "……"
                coverage = _overlap(terms, sentence)
                bonus = _intent_bonus(question, sentence)
                score = coverage * coverage * 5.0
                score += (chunk.get("vec_sim") or 0) * 0.8
                score += 0.25 / (position + 1)
                score += (chunk.get("rrf") or 0) * 1.5
                score += bonus
                heading_text = f"{chunk.get('doc_title', '')} {chunk.get('heading', '')}"
                score += _overlap(terms, heading_text) * 2.0
                if top_doc is not None and chunk.get("doc_id") == top_doc:
                    score += 0.55
                candidates.append(
                    {
                        "score": score,
                        "index": index,
                        "sentence": sentence,
                        "coverage": coverage,
                        "bonus": bonus,
                        "path": bool(re.search(r"(/|build_(?:release|sanity)|src/|tools/)", sentence)),
                    }
                )
        candidates.sort(key=lambda item: -item["score"])
        if not candidates or top_sim < self.s.min_vector_similarity:
            return Answer(REFUSAL, "insufficient")

        # 有足够「词面/句式相关」的句子时，不让纯靠排名的句子混进来
        good = [item for item in candidates if item["coverage"] > 0 or item["bonus"] > 0]
        pool = good if len(good) >= 3 else candidates
        if wants_location:
            paths = [item for item in pool if item["path"]]
            if paths:
                pool = [paths[0]] + [item for item in pool if item is not paths[0]]

        picked: list[dict[str, Any]] = []
        seen: set[str] = set()
        used_chunks: set[int] = set()
        for item in pool:
            sentence, index = item["sentence"], item["index"]
            key = sentence[:32]
            if key in seen:
                continue
            if index in used_chunks and len(picked) >= 2:
                continue
            if any(_similar(sentence, other["sentence"]) > 0.5 for other in picked):
                continue
            seen.add(key)
            used_chunks.add(index)
            picked.append(item)
            if len(picked) >= 3:
                break
        if not picked:
            return Answer(REFUSAL, "insufficient")

        docs = {
            next((c.get("doc_title") or "" for i, c in enumerate(chunks, 1) if i == item["index"]), "")
            for item in picked
        }
        if len(docs) == 1:
            head = f"根据《{next(iter(docs))}》的原文："
        else:
            head = "根据资料原文："
        lines = [head]
        for item in picked:
            lines.append(f"- {item['sentence']} [{item['index']}]")
        return Answer(
            "\n".join(lines),
            "extractive",
            reason="默认逐句摘录；勾选「详细」可让模型改写（改写仍会校验引用）",
            citations=sorted({item["index"] for item in picked}),
        )

    # ------------------------------------------------------------------ main
    def answer(
        self,
        question: str,
        chunks: list[dict[str, Any]],
        *,
        style: str = "concise",
        corpus_text: str | None = None,
    ) -> Answer:
        if not chunks:
            return Answer(REFUSAL, "insufficient", reason="没有召回到材料")
        top_sim = max((chunk.get("vec_sim") or 0) for chunk in chunks)
        terms = _terms(question)
        lexical = max(_overlap(terms, chunk["text"]) for chunk in chunks)
        unknown = _unknown_entities(question, chunks, corpus_text)
        if unknown:
            return Answer(
                REFUSAL,
                "insufficient",
                reason=f"资料中没有出现：{', '.join(unknown)}（不做语料外推断）",
            )
        if top_sim < self.s.min_vector_similarity and lexical < 0.2:
            return Answer(REFUSAL, "insufficient", reason=f"最高相似度 {top_sim:.3f}")

        if style != "detailed":
            return self._extractive(question, chunks, top_sim)

        started = time.time()
        try:
            raw = self._call_llm(self._prompt(question, chunks, style))
        except Exception as exc:  # noqa: BLE001 - 模型不可用不是致命错误
            fallback = self._extractive(question, chunks, top_sim)
            fallback.reason = f"生成模型不可用（{type(exc).__name__}: {exc}），已改用原文摘录"
            return fallback

        raw = raw.strip().strip("`").strip()
        if not raw:
            fallback = self._extractive(question, chunks, top_sim)
            fallback.reason = "生成模型返回空内容，已改用原文摘录"
            return fallback
        if "资料中没有找到足够依据" in raw[:40]:
            return Answer(REFUSAL, "insufficient", reason="模型判定材料不足")

        cited = [int(n) for n in _CITATION.findall(raw) if 1 <= int(n) <= len(chunks)]
        if not cited:
            fallback = self._extractive(question, chunks, top_sim)
            fallback.reason = "生成模型没有给出可核验引用，已改用原文摘录（更利于逐句核对）"
            return fallback
        # 去掉越界编号，保证前端不会点到不存在的证据
        def _clean(match: re.Match[str]) -> str:
            value = int(match.group(1))
            return f"[{value}]" if 1 <= value <= len(chunks) else ""

        cleaned = _CITATION.sub(_clean, raw)
        cited = sorted(set(cited))
        return Answer(
            cleaned,
            "llm",
            reason=f"{time.time() - started:.1f}s",
            citations=cited,
        )
