"""Read-only browsing and retrieval of durable document evidence."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from periplus.ingestion.objects.document import ExactDocumentRepository
from periplus.ingestion.objects.exceptions import RepositoryIntegrityError
from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.ingestion.objects.store import ObjectStore
from periplus.platform.catalogue.control import CatalogueControl, get_catalogue_control


router = APIRouter(prefix="/documents", tags=["documents"])

DocumentSort = Literal[
    "url",
    "content_type",
    "representation",
    "observed_at",
    "content_bytes",
    "stored_bytes",
]
SortDirection = Literal["asc", "desc"]

_URL_EXPRESSION = "COALESCE(visits.effective_url, visits.requested_url)"
_SORT_EXPRESSIONS: dict[DocumentSort, str] = {
    "url": _URL_EXPRESSION,
    "content_type": "documents.detected_media_type",
    "representation": "documents.representation",
    "observed_at": "documents.observed_at",
    "content_bytes": "documents.content_bytes",
    "stored_bytes": "documents.stored_bytes",
}


class DocumentListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    sort: DocumentSort = "observed_at"
    direction: SortDirection = "desc"
    content_type: str | None = Field(default=None, min_length=1, max_length=512)
    url: str | None = Field(default=None, min_length=1, max_length=4096)
    observed_from: datetime | None = None
    observed_to: datetime | None = None

    @field_validator("observed_from", "observed_to")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("document date filters must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_date_range(self) -> DocumentListQuery:
        if (
            self.observed_from is not None
            and self.observed_to is not None
            and self.observed_from >= self.observed_to
        ):
            raise ValueError("observed_from must be earlier than observed_to")
        return self


class DocumentListItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: UUID
    visit_id: UUID
    attempt_id: UUID | None
    url: str
    observed_at: datetime
    representation: Literal["response_body", "rendered_html"]
    declared_media_type: str | None
    detected_media_type: str
    charset: str | None
    content_sha256: str
    content_bytes: int
    storage_encoding: str
    stored_bytes: int


class DocumentSummary(BaseModel):
    document_count: int
    unique_content_count: int
    logical_bytes: int
    stored_bytes: int


class DocumentListResponse(BaseModel):
    items: list[DocumentListItem]
    summary: DocumentSummary
    total: int
    limit: int
    offset: int


class DocumentMediaTypesResponse(BaseModel):
    items: list[str]


class _DocumentContent(BaseModel):
    document_id: UUID
    detected_media_type: str
    charset: str | None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_bytes: int
    object_key: str
    storage_encoding: str


def get_document_store(request: Request) -> ObjectStore:
    store = getattr(request.app.state, "document_store", None)
    if not isinstance(store, ObjectStore):
        raise RuntimeError("document repository is unavailable")
    return store


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    filters: Annotated[DocumentListQuery, Query()],
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> DocumentListResponse:
    where = _where_clause(filters)
    order = _SORT_EXPRESSIONS[filters.sort]
    direction = filters.direction.upper()
    summary_sql = f"""
        WITH filtered_documents AS (
            SELECT documents.content_sha256,
                   documents.content_bytes,
                   documents.object_key,
                   documents.stored_bytes
            FROM ingest.documents AS documents
            JOIN ingest.visits AS visits USING (visit_id)
            {where}
        ),
        owned_objects AS (
            SELECT object_key, max(stored_bytes) AS stored_bytes
            FROM filtered_documents
            GROUP BY object_key
        )
        SELECT (SELECT count(*) FROM filtered_documents) AS document_count,
               (SELECT count(DISTINCT content_sha256)
                FROM filtered_documents) AS unique_content_count,
               coalesce((SELECT sum(content_bytes)
                         FROM filtered_documents), 0) AS logical_bytes,
               coalesce((SELECT sum(stored_bytes)
                         FROM owned_objects), 0) AS stored_bytes
    """
    rows_sql = f"""
        SELECT documents.document_id::VARCHAR,
               documents.visit_id::VARCHAR,
               documents.attempt_id::VARCHAR,
               {_URL_EXPRESSION} AS url,
               documents.observed_at,
               documents.representation,
               documents.declared_media_type,
               documents.detected_media_type,
               documents.charset,
               documents.content_sha256,
               documents.content_bytes,
               documents.storage_encoding,
               documents.stored_bytes
        FROM ingest.documents AS documents
        JOIN ingest.visits AS visits USING (visit_id)
        {where}
        ORDER BY {order} {direction} NULLS LAST,
                 documents.document_id {direction}
        LIMIT {filters.limit}
        OFFSET {filters.offset}
    """
    try:
        summary_rows, rows = await control.run(
            lambda _session, catalogue: (
                catalogue.trusted_remote_rows(summary_sql),
                catalogue.trusted_remote_rows(rows_sql),
            )
        )
    except duckdb.Error as exc:
        raise HTTPException(status_code=503, detail="document catalogue is unavailable") from exc

    summary_row = summary_rows[0] if summary_rows else (0, 0, 0, 0)
    summary = DocumentSummary(
        document_count=int(summary_row[0]),
        unique_content_count=int(summary_row[1]),
        logical_bytes=int(summary_row[2]),
        stored_bytes=int(summary_row[3]),
    )
    return DocumentListResponse(
        items=[
            DocumentListItem(
                document_id=row[0],
                visit_id=row[1],
                attempt_id=row[2],
                url=str(row[3]),
                observed_at=row[4],
                representation=row[5],
                declared_media_type=row[6],
                detected_media_type=str(row[7]),
                charset=row[8],
                content_sha256=str(row[9]),
                content_bytes=int(row[10]),
                storage_encoding=str(row[11]),
                stored_bytes=int(row[12]),
            )
            for row in rows
        ],
        summary=summary,
        total=summary.document_count,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.get("/media-types", response_model=DocumentMediaTypesResponse)
async def list_document_media_types(
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> DocumentMediaTypesResponse:
    try:
        rows = await control.run(
            lambda _session, catalogue: catalogue.trusted_remote_rows(
                """
                SELECT DISTINCT detected_media_type
                FROM ingest.documents
                ORDER BY detected_media_type
                """
            )
        )
    except duckdb.Error as exc:
        raise HTTPException(status_code=503, detail="document catalogue is unavailable") from exc
    return DocumentMediaTypesResponse(items=[str(row[0]) for row in rows])


@router.get("/{document_id}/content")
async def download_document(
    document_id: UUID,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
    store: Annotated[ObjectStore, Depends(get_document_store)],
) -> StreamingResponse:
    rows = await control.run(
        lambda _session, catalogue: catalogue.trusted_remote_rows(
            f"""
            SELECT document_id::VARCHAR,
                   detected_media_type,
                   charset,
                   content_sha256,
                   content_bytes,
                   object_key,
                   storage_encoding
            FROM ingest.documents
            WHERE document_id = {_sql_string(str(document_id))}
            LIMIT 1
            """
        )
    )
    if not rows:
        raise HTTPException(status_code=404, detail="document was not found")

    row = rows[0]
    document = _DocumentContent(
        document_id=row[0],
        detected_media_type=row[1],
        charset=row[2],
        content_sha256=row[3],
        content_bytes=row[4],
        object_key=row[5],
        storage_encoding=row[6],
    )
    if not await asyncio.to_thread(store.exists, document.object_key):
        raise HTTPException(status_code=404, detail="document bytes were not found")

    extension = mimetypes.guess_extension(document.detected_media_type) or ""
    filename = f"periplus-document-{document.document_id}{extension}"
    media_type = document.detected_media_type
    if document.charset is not None and media_type.startswith("text/"):
        media_type = f"{media_type}; charset={document.charset}"
    return StreamingResponse(
        _document_bytes(document, store),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(document.content_bytes),
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/by-content/{content_id}/content")
async def download_content(
    content_id: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
    store: Annotated[ObjectStore, Depends(get_document_store)],
) -> StreamingResponse:
    """Resolve public byte identity through committed retained evidence."""
    try:
        rows = await control.run(lambda _session, catalogue: catalogue.trusted_remote_rows(
            f"SELECT d.document_id FROM ingest.documents d JOIN ingest.visits v "
            f"ON v.document_id = d.document_id AND v.visit_id = d.visit_id "
            f"WHERE d.content_sha256 = {_sql_string(content_id)} "
            "AND lower(d.detected_media_type) = 'text/html' ORDER BY d.document_id LIMIT 1"
        ))
        if not rows:
            raise HTTPException(status_code=404, detail="content was not found")
        response = await download_document(UUID(str(rows[0][0])), control, store)
    except duckdb.Error as exc:
        raise HTTPException(status_code=503, detail="content catalogue is unavailable") from exc
    response.headers["Content-Disposition"] = f'attachment; filename="{content_id}"'
    response.headers["ETag"] = f'"{content_id}"'
    return response


def _where_clause(filters: DocumentListQuery) -> str:
    predicates: list[str] = []
    if filters.content_type is not None:
        predicates.append(
            "documents.detected_media_type = "
            f"{_sql_string(filters.content_type)}"
        )
    if filters.url is not None:
        predicates.append(
            f"contains(lower({_URL_EXPRESSION}), "
            f"lower({_sql_string(filters.url)}))"
        )
    if filters.observed_from is not None:
        predicates.append(
            "documents.observed_at >= "
            f"TIMESTAMPTZ {_sql_string(filters.observed_from.isoformat())}"
        )
    if filters.observed_to is not None:
        predicates.append(
            "documents.observed_at < "
            f"TIMESTAMPTZ {_sql_string(filters.observed_to.isoformat())}"
        )
    return f"WHERE {' AND '.join(predicates)}" if predicates else ""


def _document_bytes(
    document: _DocumentContent,
    store: ObjectStore,
) -> Iterator[bytes]:
    if document.storage_encoding == "zstd":
        source = RawHtmlRepository(store).iter_bytes(document.object_key)
    elif document.storage_encoding == "identity":
        source = ExactDocumentRepository(store).iter_bytes(document.object_key)
    else:
        raise RuntimeError(
            f"unsupported document storage encoding {document.storage_encoding!r}"
        )

    def verified() -> Iterator[bytes]:
        digest = hashlib.sha256()
        size_bytes = 0
        for chunk in source:
            digest.update(chunk)
            size_bytes += len(chunk)
            yield chunk
        if (
            digest.hexdigest() != document.content_sha256
            or size_bytes != document.content_bytes
        ):
            raise RepositoryIntegrityError(
                "document object metadata does not match immutable bytes"
            )

    return verified()


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
