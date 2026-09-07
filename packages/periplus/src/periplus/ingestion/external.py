"""Typed external HTML ingestion through the durable repository boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import BinaryIO
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator

from periplus.platform.config import get_int
from periplus.urls import normalize_url
from periplus.platform.catalogue import (
    DocumentRecord,
    ExternalProvenance,
    VisitEvidence,
    VisitRecord,
    document_id_for,
)
from periplus.platform.catalogue.records import canonical_json
from periplus.ingestion.queue import IngestionQueueClient
from periplus.ingestion.objects.config import object_store_from_env
from periplus.ingestion.objects.document import (
    ExactDocumentRepository,
    identify_document,
)
from periplus.ingestion.objects.store import ObjectStore


_VISIT_NAMESPACE = UUID("d967f180-4d23-55bb-b341-88ea5032e60b")


class ExternalHtmlMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_record_id: str = Field(min_length=1, max_length=2048)
    system: str = Field(min_length=1, max_length=256)
    dataset: str | None = Field(default=None, min_length=1, max_length=512)
    requested_url: str = Field(min_length=1)
    effective_url: str | None = Field(default=None, min_length=1)
    observed_at: datetime
    status_code: int | None = Field(default=None, ge=100, le=599)
    declared_media_type: str | None = Field(default="text/html", min_length=1)
    charset: str | None = Field(default=None, min_length=1)

    @field_validator("observed_at")
    @classmethod
    def require_observation_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        return value.astimezone(UTC)


class HtmlIngestionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    visit_id: UUID
    document_id: UUID
    content_sha256: str
    content_bytes: int
    object_key: str
    disposition: str
    ingestion_status: str = "pending"


class EvidenceImportService:
    """Store exact external bytes before publishing ordinary ingestion evidence."""

    def __init__(
        self,
        *,
        queue: IngestionQueueClient | None = None,
        maximum_document_bytes: int | None = None,
        object_store: ObjectStore | None = None,
    ) -> None:
        self.queue = queue or IngestionQueueClient()
        self.document_repository = ExactDocumentRepository(
            object_store or object_store_from_env()
        )
        self.maximum_document_bytes = maximum_document_bytes or get_int(
            "PERIPLUS_REPOSITORY_MAX_HTML_BYTES"
        )
        self._running = False

    async def start(self) -> None:
        await self.queue.connect()
        self._running = True

    async def close(self) -> None:
        if not self._running:
            return
        await self.queue.close()
        self._running = False

    async def ingest_external_html(
        self,
        content: BinaryIO,
        metadata: ExternalHtmlMetadata,
    ) -> HtmlIngestionResult:
        if not self._running:
            raise RuntimeError("evidence import service is not running")
        identity = canonical_json([metadata.system, metadata.dataset, metadata.source_record_id])
        visit_id = uuid5(_VISIT_NAMESPACE, identity)
        requested_url = normalize_url(metadata.requested_url)
        effective_url = (
            normalize_url(metadata.effective_url)
            if metadata.effective_url is not None
            else None
        )
        document_identity = await asyncio.to_thread(
            identify_document,
            content,
            maximum_bytes=self.maximum_document_bytes,
        )
        stored = await asyncio.to_thread(
            self.document_repository.put,
            content,
            identity=document_identity,
            source_url=effective_url or requested_url,
            visit_id=visit_id,
            observed_at=metadata.observed_at,
            content_type=metadata.declared_media_type or "text/html",
        )
        document_id = document_id_for(visit_id)
        document = DocumentRecord(
            document_id=document_id,
            visit_id=visit_id,
            attempt_id=None,
            observed_at=metadata.observed_at,
            representation="response_body",
            declared_media_type=metadata.declared_media_type,
            detected_media_type="text/html",
            charset=metadata.charset,
            content_sha256=stored.sha256,
            content_bytes=stored.size_bytes,
            object_key=stored.object_key,
            storage_encoding="identity",
            stored_bytes=stored.size_bytes,
        )
        visit = VisitRecord(
            visit_id=visit_id,
            requested_url=requested_url,
            effective_url=effective_url,
            admitted_at=metadata.observed_at,
            started_at=None,
            observed_at=metadata.observed_at,
            finished_at=metadata.observed_at,
            outcome="succeeded",
            status_code=metadata.status_code,
            document_id=document_id,
            provenance=ExternalProvenance(
                system=metadata.system,
                dataset=metadata.dataset,
                source_record_id=metadata.source_record_id,
            ),
        )
        await self.queue.enqueue_visit(
            VisitEvidence(
                visit=visit,
                attempts=(),
                document=document,
            )
        )
        return HtmlIngestionResult(
            visit_id=visit_id,
            document_id=document_id,
            content_sha256=stored.sha256,
            content_bytes=stored.size_bytes,
            object_key=stored.object_key,
            disposition="created" if stored.created else "deduplicated",
        )
