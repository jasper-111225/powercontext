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

"""Row-level CSV Sources with stable identity and text evidence projection."""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, SourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_SOURCE_VERSION = "1"


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document to split into row-level Sources."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One validated CSV record: header order and raw field text are preserved."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """Captured row evidence whose identity is derived from document and key."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def _require_trimmed(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")  # noqa: TRY003
    if value != value.strip():
        raise ValueError(f"{name} must not contain leading or trailing whitespace")  # noqa: TRY003
    return value


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Encode the document and primary key into one delimiter-safe Source identity."""

    document_id = _require_trimmed("document_id", document_id)
    if not isinstance(primary_key_value, str) or not primary_key_value.strip():
        raise ValueError("primary_key_value must be a non-empty string")  # noqa: TRY003
    source_id = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(  # noqa: TRY003
            f"csv row source identity must not exceed {MAX_SOURCE_ID_LENGTH} characters"
        )
    return source_id


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except (AttributeError, UnicodeDecodeError) as exc:
        raise ValueError("CSV content must be valid UTF-8") from exc  # noqa: TRY003


def _read_records(text: str) -> list[list[str]]:
    reader = csv.reader(
        io.StringIO(text, newline=""),
        delimiter=",",
        quotechar='"',
        doublequote=True,
        strict=True,
    )
    try:
        return list(reader)
    except csv.Error as exc:
        raise ValueError("CSV content is malformed") from exc  # noqa: TRY003


def _validate_header(header: list[str], primary_key: str) -> int:
    if not header:
        raise ValueError("CSV header must not be empty")  # noqa: TRY003
    seen: set[str] = set()
    for name in header:
        if not name.strip():
            raise ValueError("CSV header fields must not be blank")  # noqa: TRY003
        if name in seen:
            raise ValueError(f"CSV header field is duplicated: {name!r}")  # noqa: TRY003
        seen.add(name)
    if primary_key not in seen:
        raise ValueError(f"CSV header has no primary key column: {primary_key!r}")  # noqa: TRY003
    return header.index(primary_key)


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse one whole CSV document, validating every record before returning rows."""

    document_id = _require_trimmed("document_id", value.document_id)
    if not isinstance(value.primary_key, str) or not value.primary_key.strip():
        raise ValueError("primary_key must be a non-empty string")  # noqa: TRY003
    primary_key = value.primary_key

    records = _read_records(_decode(value.content))
    if not records:
        raise ValueError("CSV document is empty")  # noqa: TRY003

    header = records[0]
    key_index = _validate_header(header, primary_key)
    columns = tuple(header)

    rows: list[CSVRowInput] = []
    seen_keys: set[str] = set()
    for record in records[1:]:
        if not record:
            # A completely empty physical record carries no fields and is ignored.
            continue
        if len(record) != len(header):
            raise ValueError(  # noqa: TRY003
                f"CSV record width {len(record)} does not match header width {len(header)}"
            )
        key_value = record[key_index]
        if not key_value.strip():
            raise ValueError("CSV primary key value must not be blank")  # noqa: TRY003
        if key_value in seen_keys:
            raise ValueError(f"CSV primary key value is duplicated: {key_value!r}")  # noqa: TRY003
        seen_keys.add(key_value)
        rows.append(
            CSVRowInput(
                document_id=document_id,
                primary_key=primary_key,
                primary_key_value=key_value,
                columns=columns,
                values=tuple(record),
            )
        )
    return tuple(rows)


class CSVRowSourceAdapter:
    """Resolve and read the row-level CSV Source."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            definition_version=CSV_ROW_SOURCE_VERSION,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Expose one CSV row as canonical textual evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition(
    CSVRowSourceAdapter(),
    version=CSV_ROW_SOURCE_VERSION,
    projections=(CSVRowTextEvidenceProjection(),),
)

__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSV_ROW_SOURCE_VERSION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSVRowSourceAdapter",
    "CSVRowTextEvidenceProjection",
    "csv_row_source_id",
    "parse_csv_document",
]
