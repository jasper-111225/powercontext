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
    if value != value.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003
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


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Return up to ``limit`` exact citations, one per distinct evidence page."""

    if limit < 1:
        return ()
    page = await memory.search(
        SearchMemoryRequest(query=question, mode="fts", limit=min(max(limit * 3, limit), 100)),
    )
    citations: list[MemoryCitation] = []
    seen_sources: set[str] = set()
    for hit in page.hits:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        identities = tuple(reference.source_id for reference in entry.sources)
        if not identities or any(identity in seen_sources for identity in identities):
            continue
        seen_sources.update(identities)
        citations.append(citation)
        if len(citations) >= limit:
            break
    return tuple(citations)


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
