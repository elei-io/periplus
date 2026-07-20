"""Async bounded preparation and microbatch commit pipeline."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from types import TracebackType
from uuid import UUID
from config.performance import (
    INGEST_BATCH_MAX_BYTES,
    INGEST_BATCH_MAX_ELEMENT_ROWS,
    INGEST_BATCH_MAX_ITEMS,
    INGEST_BATCH_MAX_WAIT_SECONDS,
)

from repository.catalogue import (
    CatalogueWriteResult,
    CrawlAttemptRecord,
    CrawlRecord,
    CrawlStepRecord,
    UrlRecord,
)
from observability import repository_metrics
from repository.objects.html import HtmlIdentity
from repository.objects.artifact import ArtifactIdentity
from repository.ingestion.queue import IngestionQueueClient, projection_ingestion_request_id
from repository.service import ProjectionRebuildRequired, RepositoryIngestor


@dataclass(frozen=True, slots=True)
class IngestionWorkerConfig:
    max_items: int = 100
    max_element_rows: int = 250_000
    max_staged_bytes: int = 256 * 1024 * 1024
    max_wait_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.max_items <= 0:
            raise ValueError("ingestion batch item limit must be positive")
        if self.max_element_rows <= 0 or self.max_staged_bytes <= 0:
            raise ValueError("ingestion batch row and byte limits must be positive")
        if self.max_wait_seconds <= 0:
            raise ValueError("ingestion batch wait must be positive")

    @classmethod
    def defaults(cls) -> IngestionWorkerConfig:
        return cls(
            max_items=INGEST_BATCH_MAX_ITEMS,
            max_element_rows=INGEST_BATCH_MAX_ELEMENT_ROWS,
            max_staged_bytes=INGEST_BATCH_MAX_BYTES,
            max_wait_seconds=INGEST_BATCH_MAX_WAIT_SECONDS,
        )


class RepositoryPipeline:
    """Raw-store producer plus read boundary for the dedicated ingestion worker."""

    def __init__(
        self,
        ingestor: RepositoryIngestor,
        *,
        queue: IngestionQueueClient | None = None,
    ) -> None:
        self.ingestor = ingestor
        self.queue = queue or IngestionQueueClient()
        self._repository_lock = asyncio.Lock()
        self._running = False

    async def __aenter__(self) -> RepositoryPipeline:
        await asyncio.to_thread(self.ingestor.validate)
        try:
            await self.queue.connect()
        except BaseException:
            await asyncio.to_thread(self.ingestor.close)
            raise
        self._running = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def submit(
        self,
        *,
        captured_html: str,
        crawl: CrawlRecord,
        identity: HtmlIdentity | None = None,
    ) -> CatalogueWriteResult:
        if not self._running:
            raise RuntimeError("repository pipeline is not running")
        await self.store_raw(captured_html=captured_html, identity=identity)
        return await self.submit_stored(crawl)

    async def store_raw(
        self,
        *,
        captured_html: str,
        identity: HtmlIdentity | None = None,
    ) -> None:
        if not self._running:
            raise RuntimeError("repository pipeline is not running")
        started_at = time.perf_counter()
        try:
            stored = await asyncio.to_thread(
                self.ingestor.store_raw,
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

    async def submit_stored(
        self,
        crawl: CrawlRecord,
        *,
        urls: tuple[UrlRecord, ...],
        crawl_attempts: tuple[CrawlAttemptRecord, ...],
        request_id: str | None = None,
        crawl_steps: tuple[CrawlStepRecord, ...] | None = (),
    ) -> CatalogueWriteResult:
        if not self._running:
            raise RuntimeError("repository pipeline is not running")
        return await self.queue.submit(
            crawl,
            urls=urls,
            crawl_attempts=crawl_attempts,
            request_id=request_id,
            crawl_steps=crawl_steps,
        )

    async def store_artifact(
        self,
        *,
        content,
        identity: ArtifactIdentity,
    ) -> None:
        if not self._running:
            raise RuntimeError("repository pipeline is not running")
        started_at = time.perf_counter()
        try:
            stored = await asyncio.to_thread(
                self.ingestor.artifact_repository.put,
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

    async def enqueue_stored(
        self,
        crawl: CrawlRecord,
        *,
        urls: tuple[UrlRecord, ...],
        crawl_attempts: tuple[CrawlAttemptRecord, ...],
        request_id: str | None = None,
        crawl_steps: tuple[CrawlStepRecord, ...] = (),
    ) -> None:
        if not self._running:
            raise RuntimeError("repository pipeline is not running")
        await self.queue.enqueue(
            crawl,
            urls=urls,
            crawl_attempts=crawl_attempts,
            request_id=request_id,
            crawl_steps=crawl_steps,
        )

    async def resolve_cached_page(self, **kwargs: object):
        """Read cache state, delegating stale projection repair to the writer."""

        repaired_documents: set[str] = set()
        while True:
            try:
                async with self._repository_lock:
                    return await asyncio.to_thread(
                        self.ingestor.resolve_cached_page,
                        **kwargs,
                        repair_projection=False,
                    )
            except ProjectionRebuildRequired as exc:
                if exc.crawl.document_id in repaired_documents:
                    raise RuntimeError(
                        f"DOM projection for {exc.crawl.document_id} remained stale after rebuild"
                    ) from exc
                repaired_documents.add(exc.crawl.document_id)
                url_ids = tuple(
                    value
                    for value in (
                        exc.crawl.requested_url_id,
                        exc.crawl.final_url_id,
                    )
                    if value is not None
                )
                stored_urls = self.ingestor.catalogue_service.get_urls(url_ids)
                await self.submit_stored(
                    exc.crawl,
                    urls=tuple(stored_urls.values()),
                    crawl_attempts=(),
                    request_id=projection_ingestion_request_id(exc.crawl.document_id),
                    crawl_steps=None,
                )

    async def resolve_crawl(self, crawl_id: UUID, **kwargs: object):
        """Read a committed logical acquisition, repairing its projection via the writer."""

        repaired_documents: set[str] = set()
        resumed_pending = False
        while True:
            try:
                async with self._repository_lock:
                    hit = await asyncio.to_thread(
                        self.ingestor.resolve_crawl,
                        crawl_id,
                        repair_projection=False,
                        **kwargs,
                    )
                if hit is not None:
                    return hit
                if resumed_pending:
                    raise RuntimeError(
                        f"ingestion {crawl_id} succeeded without a visible DuckLake crawl"
                    )
                # A previous task process may have published the stable operation but
                # disappeared before its DuckLake commit became visible. Resume that
                # frozen job instead of performing a second network acquisition.
                resumed = await self.queue.resume(crawl_id)
                if resumed is None:
                    return None
                resumed_pending = True
            except ProjectionRebuildRequired as exc:
                if exc.crawl.document_id in repaired_documents:
                    raise RuntimeError(
                        f"DOM projection for {exc.crawl.document_id} remained stale after rebuild"
                    ) from exc
                repaired_documents.add(exc.crawl.document_id)
                url_ids = tuple(
                    value
                    for value in (
                        exc.crawl.requested_url_id,
                        exc.crawl.final_url_id,
                    )
                    if value is not None
                )
                stored_urls = self.ingestor.catalogue_service.get_urls(url_ids)
                await self.submit_stored(
                    exc.crawl,
                    urls=tuple(stored_urls.values()),
                    crawl_attempts=(),
                    request_id=projection_ingestion_request_id(exc.crawl.document_id),
                    crawl_steps=None,
                )

    async def projected_links(self, document_id: str, *, page_url: str):
        """Read the canonical link projection without blocking the crawl loop."""

        async with self._repository_lock:
            return await asyncio.to_thread(
                self.ingestor.catalogue_service.get_projected_links,
                document_id,
                page_url=page_url,
            )

    async def close(self) -> None:
        if not self._running:
            return
        try:
            await self.queue.close()
        finally:
            await asyncio.to_thread(self.ingestor.close)
            self._running = False
