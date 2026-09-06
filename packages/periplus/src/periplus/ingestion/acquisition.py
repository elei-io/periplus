"""Immutable-object and ingestion-queue boundary for crawlers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO
import time
from types import TracebackType
from uuid import UUID

from periplus.ingestion import metrics as repository_metrics
from periplus.platform.catalogue import VisitEvidence
from periplus.ingestion.queue import (
    IngestionQueueClient,
    get_ingestion_state,
    visit_ingestion_request_id,
)
from periplus.ingestion.objects.config import object_store_from_env
from periplus.ingestion.objects.document import (
    ExactDocumentIdentity,
    ExactDocumentRepository,
    StoredDocument,
)
from periplus.ingestion.objects.html import HtmlIdentity, RawHtmlRepository, StoredHtml


@dataclass(frozen=True, slots=True)
class AcquisitionResume:
    evidence: VisitEvidence
    repository_snapshot: int | None = None


class AcquisitionPipeline:
    """Store immutable bytes and publish visit evidence without opening DuckLake."""

    def __init__(
        self,
        *,
        queue: IngestionQueueClient | None = None,
        maximum_concurrency: int = 1,
    ) -> None:
        store = object_store_from_env(maximum_concurrency=maximum_concurrency)
        self.html_repository = RawHtmlRepository(store)
        self.document_repository = ExactDocumentRepository(store)
        self.queue = queue or IngestionQueueClient()
        self._running = False

    async def __aenter__(self) -> AcquisitionPipeline:
        await self.queue.connect()
        self._running = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def store_html(
        self,
        *,
        captured_html: str,
        source_url: str,
        visit_id: UUID,
        observed_at: datetime,
        content_type: str,
        identity: HtmlIdentity | None = None,
    ) -> StoredHtml:
        self._require_running()
        started_at = time.perf_counter()
        try:
            stored = await asyncio.to_thread(
                self.html_repository.put,
                captured_html,
                source_url=source_url,
                visit_id=visit_id,
                observed_at=observed_at,
                content_type=content_type,
                identity=identity,
            )
        except BaseException:
            repository_metrics.raw_write(
                outcome="failed",
                duration_seconds=time.perf_counter() - started_at,
                html_bytes=identity.size_bytes if identity is not None else None,
            )
            raise
        repository_metrics.raw_write(
            outcome="created" if stored.created else "deduplicated",
            duration_seconds=time.perf_counter() - started_at,
            html_bytes=stored.size_bytes,
            compressed_bytes=stored.compressed_size_bytes,
        )
        return stored

    async def store_document(
        self,
        *,
        content: BinaryIO,
        identity: ExactDocumentIdentity,
        source_url: str,
        visit_id: UUID,
        observed_at: datetime,
        content_type: str,
    ) -> StoredDocument:
        self._require_running()
        started_at = time.perf_counter()
        try:
            stored = await asyncio.to_thread(
                self.document_repository.put,
                content,
                identity=identity,
                source_url=source_url,
                visit_id=visit_id,
                observed_at=observed_at,
                content_type=content_type,
            )
        except BaseException:
            repository_metrics.raw_write(
                outcome="failed",
                duration_seconds=time.perf_counter() - started_at,
                html_bytes=identity.size_bytes,
            )
            raise
        repository_metrics.raw_write(
            outcome="created" if stored.created else "deduplicated",
            duration_seconds=time.perf_counter() - started_at,
            html_bytes=stored.size_bytes,
        )
        return stored

    async def enqueue_visit(self, evidence: VisitEvidence) -> None:
        self._require_running()
        await self.queue.enqueue_visit(evidence)

    async def resolve_visit(
        self,
        visit_id: UUID,
    ) -> AcquisitionResume | None:
        self._require_running()
        state = await get_ingestion_state(
            self.queue.results,
            visit_ingestion_request_id(visit_id),
        )
        if state is None:
            return None
        if state.job.kind != "visit" or state.job.identity != visit_id:
            raise RuntimeError(
                f"ingestion state has the wrong visit identity for {visit_id}"
            )
        assert state.job.visit is not None
        snapshot = (
            state.result.repository_snapshot
            if state.result is not None
            else None
        )
        return AcquisitionResume(
            evidence=state.job.visit,
            repository_snapshot=snapshot,
        )

    async def close(self) -> None:
        if not self._running:
            return
        await self.queue.close()
        self._running = False

    def _require_running(self) -> None:
        if not self._running:
            raise RuntimeError("acquisition pipeline is not running")
