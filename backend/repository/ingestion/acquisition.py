"""Raw-object and ingestion-queue boundary for acquisition-only workers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from types import TracebackType
from uuid import UUID

from observability import repository_metrics
from repository.catalogue import CrawlRecord, DocumentRecord
from repository.ingestion.queue import (
    IngestionQueueClient,
    crawl_ingestion_request_id,
    get_ingestion_state,
)
from repository.objects.config import object_store_from_env
from repository.objects.html import HtmlIdentity, RawHtmlRepository
from repository.objects.artifact import ArtifactIdentity, RawArtifactRepository


@dataclass(frozen=True, slots=True)
class AcquisitionResume:
    """The frozen ingestion envelope needed to settle acquisition redelivery."""

    crawl: CrawlRecord
    document: DocumentRecord | None = None
    artifact: None = None
    html: None = None
    links: None = None
    projection_rebuilt: bool = False
    repository_snapshot: int | None = None


class AcquisitionPipeline:
    """Store immutable captured content and publish ingestion without opening DuckLake."""

    def __init__(self, *, queue: IngestionQueueClient | None = None) -> None:
        store = object_store_from_env()
        self.html_repository = RawHtmlRepository(store)
        self.artifact_repository = RawArtifactRepository(store)
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

    async def store_raw(
        self,
        *,
        captured_html: str,
        identity: HtmlIdentity | None = None,
    ) -> None:
        self._require_running()
        started_at = time.perf_counter()
        try:
            stored = await asyncio.to_thread(
                self.html_repository.put,
                captured_html,
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

    async def enqueue_stored(
        self,
        crawl: CrawlRecord,
        *,
        request_id: str | None = None,
    ) -> None:
        self._require_running()
        await self.queue.enqueue(crawl, request_id=request_id)

    async def store_artifact(
        self,
        *,
        content,
        identity: ArtifactIdentity,
    ) -> None:
        self._require_running()
        started_at = time.perf_counter()
        try:
            stored = await asyncio.to_thread(
                self.artifact_repository.put,
                content,
                identity=identity,
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

    async def resolve_crawl(self, crawl_id: UUID, **_kwargs) -> AcquisitionResume | None:
        """Resolve only exact operation redelivery from NATS, never analytical cache."""

        self._require_running()
        state = await get_ingestion_state(
            self.queue.results,
            crawl_ingestion_request_id(crawl_id),
        )
        if state is None:
            return None
        if state.crawl.crawl_id != crawl_id:
            raise RuntimeError(f"ingestion state has the wrong crawl identity for {crawl_id}")
        snapshot = state.result.repository_snapshot if state.result is not None else None
        return AcquisitionResume(crawl=state.crawl, repository_snapshot=snapshot)

    async def resolve_cached_page(self, **_kwargs) -> None:
        """Cross-request DuckLake cache reads do not belong in acquisition pods."""

        self._require_running()
        return None

    async def close(self) -> None:
        if not self._running:
            return
        await self.queue.close()
        self._running = False

    def _require_running(self) -> None:
        if not self._running:
            raise RuntimeError("acquisition pipeline is not running")
