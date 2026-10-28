# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Page-level PDF Sources with full-text parsing and evidence-page retrieval."""

from __future__ import annotations

import hashlib
import io
import math
import re
from urllib.parse import quote

from pydantic import BaseModel

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, SourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class PDFDocumentInput(BaseModel):
    """One complete PDF file to split into physical pages."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical PDF page with its document identity and extracted text."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """Captured evidence for exactly one physical page of one PDF revision."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def _require_document_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    return value


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Derive one stable, bounded identity for a physical PDF page."""

    document_id = _require_document_id(document_id)
    if not isinstance(document_sha256, str) or not _SHA256_RE.fullmatch(document_sha256):
        raise ValueError("document_sha256 must be a lowercase SHA-256 hex digest")  # noqa: TRY003
    if type(page_number) is not int or page_number < 1:
        raise ValueError("page_number must be a positive integer")  # noqa: TRY003
    source_id = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        compact = hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:32]
        source_id = f"pdf:{compact}:{document_sha256}:{page_number}"
    return source_id


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of one PDF, failing atomically on any error."""

    document_id = _require_document_id(value.document_id)
    content = value.content
    if not isinstance(content, (bytes, bytearray)) or len(content) == 0:
        raise ValueError("PDF content must be non-empty bytes")  # noqa: TRY003
    raw = bytes(content)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("pypdf is required to parse PDF documents") from exc  # noqa: TRY003
    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)
    except Exception as exc:
        raise ValueError("PDF content could not be parsed") from exc  # noqa: TRY003
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF requires a user password")  # noqa: TRY003
    pages: list[PDFPageInput] = []
    try:
        for index, page in enumerate(reader.pages, start=1):
            pages.append(
                PDFPageInput(
                    document_id=document_id,
                    document_sha256=digest,
                    page_number=index,
                    text=page.extract_text() or "",
                )
            )
    except Exception as exc:
        raise ValueError("PDF pages could not be parsed") from exc  # noqa: TRY003
    if not pages:
        raise ValueError("PDF has no readable pages")  # noqa: TRY003
    return tuple(pages)


class PDFPageSourceAdapter:
    """Resolve and read the page-level PDF Source."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            definition_version=PDF_PAGE_SOURCE_VERSION,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one PDF page as canonical textual evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = AdapterSourceDefinition(
    PDFPageSourceAdapter(),
    version=PDF_PAGE_SOURCE_VERSION,
    projections=(PDFPageTextEvidenceProjection(),),
)


_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does", "for", "from", "has",
        "have", "how", "in", "is", "it", "of", "on", "or", "that", "the", "their", "this", "to",
        "was", "were", "what", "when", "where", "which", "who", "why", "with", "fy", "year", "any",
        "if", "not", "then", "state", "define", "give", "answer", "respond", "question", "assuming",
        "perspective", "use", "used", "using", "shown", "within", "details", "please", "explain",
    }
)
_WORD_RE = re.compile(r"[0-9][0-9,.]*|[A-Za-z][A-Za-z0-9_.\-]*")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Offline query expansion for finance questions: intent phrases mapped to the vocabulary that
# annual reports actually use. These are generic domain terms, not document or answer facts.
_EXPANSION_RULES: tuple[tuple[str, tuple[str, ...], float], ...] = (
    (
        r"(quick ratio|current ratio|liquidity|working capital|liquid)",
        ("balance sheets", "current assets", "current liabilities", "cash and cash equivalents",
         "short-term investments", "accounts receivable", "inventories", "total current assets",
         "total current liabilities"),
        1.0,
    ),
    (
        r"(inventory turnover|inventories|inventory)",
        ("inventories", "cost of sales", "cost of products sold", "cost of revenue", "cost of goods sold"),
        1.0,
    ),
    (r"(receivable|receivables|days sales)", ("accounts receivable", "net sales", "net revenue"), 0.9),
    (
        r"(gross margin|gross profit|gross income)",
        ("gross profit", "cost of sales", "cost of products", "cost of services", "cost of products sold",
         "cost of revenue", "sales of products", "sales of services", "net sales", "gross margin"),
        1.0,
    ),
    (
        r"(operating margin|operating income|operating profit|operating loss)",
        ("operating income", "operating profit", "income from operations", "results of operations",
         "operating expenses"),
        1.0,
    ),
    (r"(net margin|net income|net earnings|profitability|bottom line)",
     ("net income", "net earnings", "net income attributable"), 0.9),
    (r"(ebitda|depreciation|amortization)",
     ("operating income", "depreciation", "amortization", "cash flows"), 1.0),
    (r"(capex|capital expenditure|capital expenditures)",
     ("capital expenditures", "purchases of property", "property plant and equipment", "investing activities"), 1.0),
    (
        r"(cash flow|cash flows|operating activities|investing activities|financing activities)",
        ("cash flows", "operating activities", "investing activities", "financing activities", "net cash"),
        1.0,
    ),
    (r"(debt|leverage|borrowings|borrowing|credit facilities|notes payable)",
     ("long-term debt", "total debt", "borrowings", "notes payable", "debt", "credit facilities"), 1.0),
    (r"(dividend|dividends)", ("dividends", "dividend"), 1.0),
    (r"(repurchase|buyback|share repurchase)", ("repurchase", "share repurchase", "treasury stock"), 1.0),
    (r"(segment|segments|business unit)",
     ("business segment", "segment", "segments", "performance by business segment"), 1.0),
    (r"(geograph|geographies|region|regions|international)",
     ("geographic", "united states", "north america", "international", "asia pacific", "europe",
      "latin america"), 0.9),
    (r"(legal|litigation|lawsuit|proceedings|battles|claims)",
     ("legal proceedings", "litigation", "legal", "claims", "contingencies"), 1.0),
    (r"(tax|taxes|effective tax rate)",
     ("effective tax rate", "provision for income taxes", "income taxes", "tax rate"), 1.0),
    (r"(employee|employees|headcount|workforce)", ("employees", "workforce"), 1.0),
    (r"(product|products|service|services)", ("products", "services", "product"), 0.8),
    (r"(customer|customers|concentration)", ("customers", "customer", "concentration", "major customer"), 1.0),
    (r"(cyclical|cyclicality|economic conditions|recession)",
     ("cyclical", "cyclicality", "economic conditions", "global economic"), 1.0),
    (r"(backlog|orders)", ("backlog", "orders", "order book"), 1.0),
    (r"(production rate|rate|rates|ramp)", ("production rate", "rate", "production", "deliveries"), 0.7),
    (r"(restructur|impairment|goodwill|write-?off)",
     ("restructuring", "impairment", "goodwill", "charges"), 1.0),
    (r"(pension|retiree|postretirement)", ("pension", "retirement", "postretirement"), 1.0),
    (r"(research and development|r&d)", ("research and development", "research", "development"), 1.0),
    (r"(marketing|advertising|promotion)", ("advertising", "marketing", "promotion"), 1.0),
    (r"(seasonal|seasonality)", ("seasonal", "seasonality"), 1.0),
    (r"(competit|competitor|competition)", ("competition", "competitors", "competitive"), 1.0),
    (r"(supply chain|supplier|suppliers|raw material)",
     ("supply chain", "suppliers", "raw materials", "commodity"), 1.0),
    (r"(risk|risks|risk factors)", ("risk factors", "risks", "risk"), 0.8),
    (r"(acquisition|acquisitions|merger|m&a|divestiture)",
     ("acquisition", "acquisitions", "merger", "divestiture", "business combination"), 1.0),
    (r"(cyber|cybersecurity|data security)", ("cybersecurity", "cyber", "information security"), 1.0),
    (r"(lease|leases|leasing)", ("leases", "lease", "operating lease", "right-of-use"), 1.0),
    (r"(share|shares|earnings per share|eps|diluted)",
     ("earnings per share", "shares", "diluted", "weighted-average"), 0.8),
    (r"(return on equity|return on assets|roe|roa|asset turnover)",
     ("net income", "total assets", "stockholders equity", "sales"), 0.9),
    (r"(license|licensing|royalt)", ("license", "royalty", "licensing"), 0.9),
    (r"(sustainab|esg|environment|climate|carbon)",
     ("sustainability", "environment", "climate", "emissions"), 1.0),
    (r"(insurance|self-insurance)", ("insurance", "self-insurance"), 0.9),
    (r"(warrant|warranty|guarantee)", ("warranty", "guarantee"), 0.9),
    (r"(contract|contracts|obligation|obligations)", ("contract", "contracts", "obligations"), 0.8),
    (r"(growth|growing|high growth|grow)",
     ("results of operations", "net sales", "sales", "increase", "decrease", "growth"), 0.8),
    (r"(interest expense|interest income|interest)", ("interest expense", "interest income", "interest"), 0.9),
    (r"(tariff|tariffs|trade|trade policy)", ("tariffs", "duties", "trade", "customs"), 1.0),
    (r"(inflation|inflationary|pricing|price increases)", ("inflation", "pricing", "price", "cost"), 0.9),
    (r"(currency|foreign exchange|foreign currency|exchange rate)",
     ("foreign currency", "currency", "exchange rate", "translation"), 0.9),
    (r"(stock-based|share-based|stock compensation)",
     ("share-based compensation", "stock-based compensation", "compensation expense"), 1.0),
    (r"(environmental|remediation|climate)", ("environmental", "remediation", "climate"), 0.9),
    (r"(contractual obligations|commitments)", ("contractual obligations", "commitments", "purchase obligations"), 1.0),
    (r"(increase|increased|decrease|decreased|improve|improving|decline|declined)", ("increase", "decrease"), 0.4),
    (r"(capital.?intensive)",
     ("property plant and equipment", "capital expenditures", "depreciation", "total assets",
      "investing activities"), 1.0),
    (r"(product categor|service categor|product categories|service categories)",
     ("business segment", "segment data", "revenues"), 1.0),
    (r"(major products|products and services|what are the major)",
     ("overview", "business", "products", "services"), 0.8),
)
_COMPILED_EXPANSIONS = tuple((re.compile(pattern), terms, weight) for pattern, terms, weight in _EXPANSION_RULES)
_STATEMENT_PRIORS: tuple[tuple[str, str], ...] = (
    (r"(quick ratio|current ratio|liquidity|working capital|inventory turnover|receivable|debt)",
     r"(balance sheet|consolidated balance)"),
    (r"(revenue|sales|margin|income|earnings|eps|tax|expense|capital.?intensive)",
     r"(statement of income|statements of earnings|statement of operations|statements of operations|"
     r"results of operations|income statement)"),
    (r"(cash flow|capex|capital expenditure|dividend|repurchase|capital.?intensive)", r"(cash flow)"),
    (r"(segment|business unit|product categor|service categor)",
     r"(business segment|segment data|segment information)"),
    (r"(legal|litigation|proceedings)", r"(legal proceedings|commitments|contingencies)"),
    (r"(tax|taxes)", r"(income tax)"),
    (r"(debt|borrowing)", r"(note.*debt|long-term debt|total debt)"),
)
_COMPILED_PRIORS = tuple((re.compile(intent), re.compile(statement)) for intent, statement in _STATEMENT_PRIORS)


def _salient_terms(question: str) -> tuple[str, ...]:
    terms: list[str] = []
    for match in _WORD_RE.finditer(question):
        term = match.group(0).casefold().strip(".,")
        if len(term) < 2 or term in _STOPWORDS:
            continue
        for expanded in _expand_year(term):
            if expanded not in terms:
                terms.append(expanded)
    return tuple(terms)


def _expand_year(term: str) -> tuple[str, ...]:
    match = re.fullmatch(r"fy(\d{4})", term)
    return (match.group(1),) if match else (term,)


def _expansions(question: str) -> dict[str, float]:
    text = question.casefold()
    values: dict[str, float] = {}
    for pattern, terms, weight in _COMPILED_EXPANSIONS:
        if pattern.search(text):
            for term in terms:
                values[term] = max(values.get(term, 0.0), weight)
    return values


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.casefold())


def _heading_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()][:8]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("Item ", "Note ")) and len(stripped) < 90:
            lines.append(stripped)
    return " ".join(lines).casefold()


def _count(term: str, tokens: list[str], counter: dict[str, int], normalized: str) -> int:
    if " " in term:
        return normalized.count(f" {term} ")
    return counter.get(term, 0)


_GENERIC_DOCUMENT_TOKENS = frozenset(
    {"10k", "10q", "8k", "2020", "2021", "2022", "2023", "2024", "2025", "inc", "corp",
     "corporation", "company", "the", "and", "dated"}
)
_DOCUMENT_ALIASES = {"jnj": "johnson", "coke": "coca", "p&g": "procter", "pg": "procter"}


def _document_id(text: str) -> str:
    return text.split("\n", 1)[0]


def _filter_documents(texts: dict[tuple[str, str], str], question: str) -> dict[tuple[str, str], str]:
    """Keep only the document the question names, when its company token is unambiguous."""

    question_tokens = set(_tokens(question))
    for alias, canonical in _DOCUMENT_ALIASES.items():
        if alias in question_tokens:
            question_tokens.add(canonical)
    documents = {_document_id(text) for text in texts.values()}
    matched = {
        document
        for document in documents
        if (set(_tokens(document)) - _GENERIC_DOCUMENT_TOKENS) & question_tokens
    }
    if not matched or len(matched) >= len(documents):
        return texts
    return {key: text for key, text in texts.items() if _document_id(text) in matched}


def _rank_candidates(
    texts: dict[tuple[str, str], str],
    terms: tuple[str, ...],
    expansions: dict[str, float],
    question: str,
) -> list[tuple[str, str]]:
    """Rank retrieved entries with an offline lexical relevance model."""

    weights: dict[str, float] = {term: 1.0 for term in terms}
    for term, weight in expansions.items():
        weights[term] = max(weights.get(term, 0.0), 0.7 * weight)
    if not weights:
        return list(texts)
    documents: dict[tuple[str, str], list[str]] = {key: _tokens(text) for key, text in texts.items()}
    total = max(1, len(documents))
    document_frequency: dict[str, int] = {}
    for tokens in documents.values():
        for token in set(tokens):
            document_frequency[token] = document_frequency.get(token, 0) + 1
    idf = {
        term: math.log(1.0 + (total - document_frequency.get(term, 0) + 0.5) / (document_frequency.get(term, 0) + 0.5))
        for term in weights
    }
    average_length = sum(len(tokens) for tokens in documents.values()) / total
    query_weights = {term: weight * idf[term] for term, weight in weights.items()}
    query_norm = math.sqrt(sum(value * value for value in query_weights.values())) or 1.0
    lowered = question.casefold()
    priors = [(intent, statement) for intent, statement in _COMPILED_PRIORS if intent.search(lowered)]
    cosine: dict[tuple[str, str], float] = {}
    bm25: dict[tuple[str, str], float] = {}
    for key, tokens in documents.items():
        counter: dict[str, int] = {}
        for token in tokens:
            counter[token] = counter.get(token, 0) + 1
        normalized = f" {' '.join(tokens)} "
        length = len(tokens)
        cosine_value = 0.0
        bm25_value = 0.0
        projections: dict[str, float] = {}
        for term, weight in weights.items():
            count = _count(term, tokens, counter, normalized)
            if not count:
                continue
            projections[term] = query_weights[term] * (1.0 + math.log(count))
            bm25_value += (
                weight * idf[term] * (count * 2.2) / (count + 1.2 * (0.25 + 0.75 * length / max(1.0, average_length)))
            )
        if projections:
            projection_norm = math.sqrt(sum(value * value for value in projections.values())) or 1.0
            cosine_value = 10.0 * sum(query_weights[term] * value for term, value in projections.items()) / (
                query_norm * projection_norm
            )
        heading = f" {' '.join(_tokens(_heading_text(texts[key])))} "
        for term, weight in weights.items():
            if f" {term} " in heading:
                cosine_value += weight * idf[term]
                bm25_value += 0.6 * weight * idf[term]
        for intent, statement in priors:
            if statement.search(heading):
                cosine_value += 3.0
                bm25_value += 3.0
        cosine[key] = cosine_value
        bm25[key] = bm25_value
    cosine_max = max(cosine.values()) or 1.0
    bm25_max = max(bm25.values()) or 1.0
    combined = {
        key: cosine[key] / cosine_max + bm25[key] / bm25_max
        for key in cosine
    }
    return sorted(combined, key=lambda key: (-combined[key], key))


def _page_identity(entry: object) -> tuple[str, ...]:
    identities: list[str] = []
    for reference in getattr(entry, "sources", ()):
        source_id = getattr(reference, "source_id", "")
        if not source_id:
            continue
        parts = source_id.split(":")
        identities.append(parts[-2] + ":" + parts[-1] if len(parts) >= 4 else source_id)
    return tuple(identities) or ("",)


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Return up to ``limit`` exact citations, one per distinct evidence page."""

    if limit < 1 or not question.strip():
        return ()
    terms = _salient_terms(question)
    expansions = _expansions(question)
    variants: list[str] = [question]
    seen: set[str] = set()
    for text in (*terms, *expansions):
        folded = text.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        variants.append(text)
    depth = max(40, min(limit * 6, 100))
    texts: dict[tuple[str, str], str] = {}
    citations: dict[tuple[str, str], MemoryCitation] = {}
    for variant in variants[:40]:
        page = await memory.search(SearchMemoryRequest(query=variant, mode="fts", limit=depth))
        for hit in page.hits:
            key = (hit.memory_ref.artifact_id, hit.entry_id)
            if key in texts:
                continue
            texts[key] = hit.text
            citations[key] = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
    if not texts:
        return ()
    texts = _filter_documents(texts, question)
    ranked = _rank_candidates(texts, terms, expansions, question)
    results: list[MemoryCitation] = []
    pages: set[tuple[str, ...]] = set()
    for key in ranked:
        if len(results) >= limit:
            break
        citation = citations[key]
        try:
            entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        except Exception:  # noqa: BLE001 - an entry that cannot be read back is not citable
            continue
        identity = _page_identity(entry)
        if identity in pages:
            continue
        pages.add(identity)
        results.append(citation)
    return tuple(results)


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDF_PAGE_SOURCE_VERSION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDFPageSourceAdapter",
    "PDFPageTextEvidenceProjection",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
