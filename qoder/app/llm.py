from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import httpx

from .config import Settings, openai_url

INSUFFICIENT_ANSWER = (
    "资料中没有找到足够依据来回答这个问题。换一种问法，或把这个主题的资料导入资料库后再试。"
)
NO_ANSWER_MARKER = "资料中没有找到足够依据"
_CITATION = re.compile(r"\[(\d{1,2})\]")
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
_WORD = re.compile(r"[a-z0-9][a-z0-9_\-.]*")
_ENTITY = re.compile(r"[A-Za-z][A-Za-z0-9_+\-.]*")
_STOP_GRAMS = {
    "什么", "怎么", "怎样", "如何", "为何", "为什么", "哪些", "哪个", "哪里",
    "多少", "多久", "是否", "可以", "请问", "一下", "告诉", "介绍", "以及",
    "这个", "那个", "我们", "你们", "他们", "它们", "的话", "一个", "有没有",
    "需要", "应该", "能够", "进行", "关于", "相关",
}
_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "to", "of",
    "and", "or", "in", "on", "at", "for", "with", "how", "what", "why",
    "which", "who", "when", "where", "does", "do", "did", "can", "could",
    "should", "would", "i", "you", "it", "this", "that", "there", "from",
}


def lexical_terms(text: str) -> set[str]:
    lowered = text.lower()
    terms: set[str] = set()
    for run in _CJK_RUN.findall(lowered):
        if len(run) == 1:
            terms.add(run)
            continue
        for index in range(len(run) - 1):
            gram = run[index : index + 2]
            if gram not in _STOP_GRAMS:
                terms.add(gram)
    for word in _WORD.findall(lowered):
        if len(word) >= 2 and word not in _STOP_WORDS:
            terms.add(word)
    return terms


def lexical_coverage(query: str, texts: Sequence[str]) -> float:
    terms = lexical_terms(query)
    if not terms:
        return 0.0
    haystack = "\n".join(texts).lower()
    hits = sum(1 for term in terms if term in haystack)
    return hits / len(terms)


def entity_tokens(query: str) -> list[str]:
    tokens: list[str] = []
    for match in _ENTITY.finditer(query):
        token = match.group(0).lower().strip(".-")
        if len(token) < 2 or token in _STOP_WORDS:
            continue
        if token.isdigit():
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def confidence_of(top_vec_sim: float | None, coverage: float) -> float:
    return min(1.0, max(0.0, top_vec_sim or 0.0) * 0.75 + coverage * 0.45)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.model = settings.llm_model
        self._client = client

    def _post(self, system: str, user: str, max_tokens: int = 600) -> str:
        url = openai_url(self.settings.llm_base, "chat/completions")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "stream": False,
        }
        try:
            if self._client is not None:
                response = self._client.post(url, json=payload)
            else:
                with httpx.Client(timeout=self.settings.llm_timeout) as client:
                    response = client.post(url, json=payload)
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise LLMError("生成模型返回空内容")
            return content.strip()
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(
                f"调用生成模型失败（{self.model} @ {self.settings.llm_base}）: {exc}"
            ) from exc

    def generate(self, system: str, user: str) -> str:
        return self._post(system, user)

    def available(self) -> tuple[bool, str]:
        base = self.settings.llm_base.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        for url in (f"{base}/api/tags", openai_url(self.settings.llm_base, "models")):
            try:
                if self._client is not None:
                    response = self._client.get(url, timeout=2.0)
                else:
                    response = httpx.get(url, timeout=2.0)
                if response.status_code >= 400:
                    continue
                if url.endswith("/api/tags"):
                    models = response.json().get("models", [])
                    names = {str(item.get("name", "")) for item in models if isinstance(item, dict)}
                    if any(name == self.model or name.startswith(self.model + ":") for name in names):
                        return True, f"已安装 {self.model}"
                    return False, f"Ollama 可达，但未找到模型 {self.model}"
                return True, "Ollama 可达"
            except Exception:
                continue
        return False, "生成模型不可用，将使用原文摘录"


def _system_prompt() -> str:
    return (
        "你是「据典」，一个只依据本地资料作答的证据助手。必须遵守：\n"
        "1. 只能使用 <evidence> 中提供的材料，不得使用外部知识，不得编造。\n"
        "2. 每个事实性句子后面必须标注来源编号，如 [1]、[2]；编号只能来自材料编号。\n"
        "3. 如果材料不足以回答，只输出这一句，不要补充：\n"
        f"{INSUFFICIENT_ANSWER}\n"
        "4. 不要引用材料以外的内容，不要把用户偏好当成事实依据。\n"
        "5. 使用简体中文；引用原文时不要改动数字、命令、路径和专有名词。"
    )


def _format_evidence(evidence: Sequence[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, row in enumerate(evidence, start=1):
        meta = f"《{row.get('doc_title', '')}》"
        if row.get("heading"):
            meta += f" › {row['heading']}"
        if row.get("page"):
            meta += f"（第 {row['page']} 页）"
        blocks.append(f"[{index}] {meta}\n{row.get('text', '')}")
    return "\n\n".join(blocks)


def build_prompt(
    question: str,
    evidence: Sequence[dict[str, Any]],
    profile: dict[str, Any] | None = None,
    style: str = "concise",
) -> tuple[str, str]:
    user_parts: list[str] = []
    if profile:
        fields = []
        if profile.get("topic"):
            fields.append(f"研究主题：{profile['topic']}")
        if profile.get("goal"):
            fields.append(f"本周目标：{profile['goal']}")
        if profile.get("style"):
            fields.append(f"偏好：{profile['style']}")
        if fields:
            user_parts.append("<user_profile>\n" + "\n".join(fields) + "\n</user_profile>")
    user_parts.append("<evidence>\n" + _format_evidence(evidence) + "\n</evidence>")
    instruction = "请依据材料回答，并逐句标注引用编号。"
    if style == "detailed":
        instruction += "在不超出材料的前提下，给出稍详细的解释。"
    else:
        instruction += "回答保持简洁，通常不超过 4 句。"
    user_parts.append(f"问题：{question}\n{instruction}")
    return _system_prompt(), "\n\n".join(user_parts)


def _rows_for_trace(rows: Sequence[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for rank, row in enumerate(rows[:limit], start=1):
        trace.append(
            {
                "rank": rank,
                "chunk_id": int(row["id"]),
                "doc_title": row.get("doc_title", ""),
                "heading": row.get("heading", ""),
                "page": row.get("page"),
                "vec_sim": row.get("vec_sim"),
                "ft_score": row.get("ft_score"),
                "rrf": row.get("rrf"),
                "snippet": str(row.get("text", ""))[:120],
            }
        )
    return trace


def _citation(row: dict[str, Any], n: int, used: bool) -> dict[str, Any]:
    return {
        "n": n,
        "chunk_id": int(row["id"]),
        "doc_id": int(row["doc_id"]),
        "doc_title": row.get("doc_title", ""),
        "source": row.get("source", ""),
        "heading": row.get("heading", ""),
        "page": row.get("page"),
        "text": row.get("text", ""),
        "vec_rank": row.get("vec_rank"),
        "ft_rank": row.get("ft_rank"),
        "vec_sim": row.get("vec_sim"),
        "ft_score": row.get("ft_score"),
        "rrf": row.get("rrf"),
        "used": used,
    }


def parse_citations(text: str, max_n: int) -> tuple[list[int], list[int]]:
    valid: list[int] = []
    invalid: list[int] = []
    for match in _CITATION.finditer(text):
        number = int(match.group(1))
        if 1 <= number <= max_n:
            if number not in valid:
                valid.append(number)
        elif number not in invalid:
            invalid.append(number)
    return valid, invalid


def _clean_citations(text: str, max_n: int) -> str:
    def replace(match: re.Match[str]) -> str:
        number = int(match.group(1))
        return match.group(0) if 1 <= number <= max_n else ""

    cleaned = _CITATION.sub(replace, text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _sentence_units(text: str) -> list[str]:
    """Split a chunk into sentence-like units, folding code fences into a
    preceding line ending with a colon so command/path evidence stays usable."""
    lines = str(text).replace("\r\n", "\n").split("\n")
    units: list[str] = []
    buffer: list[str] = []
    fence: list[str] = []
    in_fence = False
    marker = ""

    def flush_prose() -> None:
        block = "\n".join(buffer).strip()
        buffer.clear()
        if not block:
            return
        for match in re.finditer(r"[^。！？!?；;\n]+[。！？!?；;]?", block):
            sentence = match.group(0).strip()
            if sentence:
                units.append(sentence)

    def flush_fence() -> None:
        block = [
            line.strip()
            for line in fence
            if line.strip() and not line.lstrip().startswith(("```", "~~~"))
        ]
        fence.clear()
        if not block:
            return
        code = block[0]
        if units and units[-1].rstrip().endswith(("：", ":")):
            units[-1] = f"{units[-1].rstrip()} {code}"
        # Code without an explanatory lead-in is not quoted as prose.

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            current = stripped[:3]
            if not in_fence:
                flush_prose()
                in_fence = True
                marker = current
                fence.append(line)
            else:
                fence.append(line)
                if current == marker:
                    in_fence = False
                    marker = ""
                    flush_fence()
            continue
        if in_fence:
            fence.append(line)
        elif line.strip():
            buffer.append(line)
        else:
            flush_prose()
    flush_prose()
    if fence:
        flush_fence()
    return [unit for unit in units if len(unit) >= 4]


def _trigrams(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", text.lower())
    return {compact[index : index + 3] for index in range(max(0, len(compact) - 2))}


def _similar(a: str, b: str) -> bool:
    left, right = _trigrams(a), _trigrams(b)
    if not left or not right:
        return a == b
    return len(left & right) / len(left | right) > 0.5


def _intent_bonus(question: str, sentence: str) -> float:
    bonus = 0.0
    lowered = sentence.lower()
    if re.search(r"(在哪|哪个目录|什么目录|路径|位于|output)", question):
        if re.search(r"(/|\\|build|\.so|\.a|目录)", lowered):
            bonus += 0.30
    if re.search(r"(怎么|如何|怎样|命令|步骤)", question):
        if re.search(r"(\./|make|gdb|build\.sh|obd\.sh|命令|执行|使用)", lowered):
            bonus += 0.18
    if re.search(r"(多少|多久|几个|比例|百分比|大小|上限|下限)", question):
        if re.search(r"\d", sentence):
            bonus += 0.30
    if re.search(r"(为什么|原因|为何)", question):
        if re.search(r"(因为|由于|原因|避免|防止)", sentence):
            bonus += 0.22
    if re.search(r"(什么时候|何时|何时会)", question):
        if re.search(r"(当|时|之后|之前|首次|结束)", sentence):
            bonus += 0.15
    return bonus


def _extractive(
    question: str,
    evidence: Sequence[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    candidates: list[tuple[float, int, str, dict[str, Any]]] = []
    for index, row in enumerate(evidence):
        units = _sentence_units(row.get("text", ""))
        for sentence in units:
            coverage = lexical_coverage(question, [sentence])
            vector = float(row.get("vec_sim") or 0.0)
            rank_bonus = 0.08 * (1.0 - index / max(1, len(evidence)))
            bonus = _intent_bonus(question, sentence)
            score = coverage * coverage + max(0.0, vector) * 0.25 + rank_bonus + bonus
            if coverage > 0 or bonus > 0 or index == 0:
                candidates.append((score, index, sentence, row))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected: list[tuple[int, str, dict[str, Any]]] = []
    for _, index, sentence, row in candidates:
        if any(_similar(sentence, existing[1]) for existing in selected):
            continue
        selected.append((index, sentence, row))
        if len(selected) >= 3:
            break

    if not selected:
        row = evidence[0]
        quote_lines = [line for line in str(row.get("text", "")).split("\n") if line.strip()]
        quote = "\n".join(quote_lines[:4]).strip()
        selected = [(0, quote, row)]

    used_numbers: list[int] = []
    bullets: list[str] = []
    for index, sentence, row in selected:
        number = index + 1
        if number not in used_numbers:
            used_numbers.append(number)
        bullets.append(f"- {sentence.rstrip()} [{number}]")

    titles = {row.get("doc_title", "") for _, _, row in selected}
    if len(titles) == 1 and next(iter(titles)):
        header = f"根据《{next(iter(titles))}》的原文："
    else:
        header = "根据资料原文："
    answer = header + "\n" + "\n".join(bullets)

    citations = [
        _citation(evidence[number - 1], number, True)
        for number in sorted(used_numbers)
    ]
    return {
        "mode": "extractive",
        "answer": answer,
        "citations": citations,
        "reason": reason,
        "used_numbers": used_numbers,
    }


@dataclass
class Answer:
    mode: str
    answer: str
    citations: list[dict[str, Any]]
    reason: str
    confidence: float
    evidence: list[dict[str, Any]]
    trace: dict[str, Any]
    llm_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "answer": self.answer,
            "citations": self.citations,
            "reason": self.reason,
            "confidence": round(self.confidence, 4),
            "evidence": self.evidence,
            "trace": self.trace,
            "llm_error": self.llm_error,
        }


def answer_question(
    question: str,
    fused: Sequence[dict[str, Any]],
    vector_rows: Sequence[dict[str, Any]],
    fulltext_rows: Sequence[dict[str, Any]],
    *,
    settings: Settings,
    llm: LLMClient,
    corpus_contains: Callable[[str], bool] | None = None,
    profile: dict[str, Any] | None = None,
    style: str = "concise",
) -> Answer:
    evidence = [dict(row) for row in fused[: settings.evidence_top_n]]
    trace = {
        "vector": _rows_for_trace(vector_rows, 10),
        "fulltext": _rows_for_trace(fulltext_rows, 10),
        "rrf_k": settings.rrf_k,
    }

    top_sims = [float(row["vec_sim"]) for row in evidence if row.get("vec_sim") is not None]
    top_vec_sim = max(top_sims) if top_sims else None
    top_texts = [str(row.get("text", "")) for row in evidence[:3]]
    coverage = lexical_coverage(question, top_texts)
    confidence = confidence_of(top_vec_sim, coverage)

    missing: list[str] = []
    if corpus_contains is not None:
        for token in entity_tokens(question):
            try:
                if not corpus_contains(token):
                    missing.append(token)
            except Exception:
                break

    if not evidence:
        return Answer(
            mode="insufficient",
            answer=INSUFFICIENT_ANSWER,
            citations=[],
            reason="资料库中没有检索到相关片段",
            confidence=0.0,
            evidence=[],
            trace=trace,
        )

    if missing:
        return Answer(
            mode="insufficient",
            answer=INSUFFICIENT_ANSWER,
            citations=[],
            reason="资料中没有出现：" + "、".join(missing) + "（不做语料外推断）",
            confidence=confidence,
            evidence=[_citation(row, index, False) for index, row in enumerate(evidence, 1)],
            trace=trace,
        )

    if (top_vec_sim or 0.0) < settings.min_vector_sim and coverage < settings.min_coverage:
        return Answer(
            mode="insufficient",
            answer=INSUFFICIENT_ANSWER,
            citations=[],
            reason=(
                f"证据不足：最高向量相似度 {0.0 if top_vec_sim is None else top_vec_sim:.2f}，"
                f"词面覆盖 {coverage:.2f}"
            ),
            confidence=confidence,
            evidence=[_citation(row, index, False) for index, row in enumerate(evidence, 1)],
            trace=trace,
        )

    llm_error: str | None = None
    if settings.llm_enabled:
        system, user = build_prompt(question, evidence, profile=profile, style=style)
        try:
            raw = llm.generate(system, user)
        except LLMError as exc:
            raw = ""
            llm_error = str(exc)
        if raw:
            head = raw[:40]
            if NO_ANSWER_MARKER in head:
                return Answer(
                    mode="insufficient",
                    answer=INSUFFICIENT_ANSWER,
                    citations=[],
                    reason="生成模型判断材料不足以回答",
                    confidence=confidence,
                    evidence=[_citation(row, index, False) for index, row in enumerate(evidence, 1)],
                    trace=trace,
                )
            valid, _invalid = parse_citations(raw, len(evidence))
            if not valid:
                result = _extractive(
                    question, evidence, "模型未给出合法引用，已回退为原文摘录"
                )
            else:
                cleaned = _clean_citations(raw, len(evidence))
                citations = [
                    _citation(evidence[number - 1], number, True) for number in valid
                ]
                return Answer(
                    mode="llm",
                    answer=cleaned,
                    citations=citations,
                    reason="",
                    confidence=confidence,
                    evidence=[
                        _citation(row, index, index in valid)
                        for index, row in enumerate(evidence, 1)
                    ],
                    trace=trace,
                    llm_error=None,
                )
        else:
            result = _extractive(question, evidence, "生成模型不可用，已回退为原文摘录")

    else:
        result = _extractive(question, evidence, "未启用生成模型，使用原文摘录")

    return Answer(
        mode=result["mode"],
        answer=result["answer"],
        citations=result["citations"],
        reason=result["reason"],
        confidence=confidence,
        evidence=[
            _citation(row, index, index in result["used_numbers"])
            for index, row in enumerate(evidence, 1)
        ],
        trace=trace,
        llm_error=llm_error,
    )
