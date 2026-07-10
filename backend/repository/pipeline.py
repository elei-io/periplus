"""Async bounded preparation and microbatch commit pipeline."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from types import TracebackType
from uuid import UUID

from repository.ducklake import (
    CatalogueWriteResult,
    CrawlRecord,
    RunCrawlUsageRecord,
    RunManifestRecord,
)
from observability import repository_metrics
from repository.html import HtmlIdentity
from repository.queue import IngestionQueueClient, projection_ingestion_request_id
from repository.service import ProjectionRebuildRequired, RepositoryIngestor


@dataclass(frozen=True, slots=True)
class IngestionWorkerConfig:
    max_items: int = 100
    max_element_rows: int = 250_000
    max_staged_bytes: int = 256 * 1024 * 1024
    max_wait_seconds: float = 0.5

    @classmethod
    def from_env(cls) -> IngestionWorkerConfig:
        return cls(
            max_items=_positive_int("ATLAS_INGEST_BATCH_ITEMS", 100),
            max_element_rows=_positive_int("ATLAS_INGEST_BATCH_ELEMENT_ROWS", 250_000),
            max_staged_bytes=_positive_int(
                "ATLAS_INGEST_BATCH_BYTES", 256 * 1024 * 1024
            ),
            max_wait_seconds=_positive_float("ATLAS_INGEST_BATCH_WAIT_SECONDS", 0.5),
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
        run_manifest: RunManifestRecord | None = None,
        run_usage: RunCrawlUsageRecord | None = None,
    ) -> CatalogueWriteResult:
        if not self._running:
            raise RuntimeError("repository pipeline is not running")
        await self.store_raw(captured_html=captured_html, identity=identity)
        return await self.submit_stored(
            crawl,
            run_manifest=run_manifest,
            run_usage=run_usage,
        )

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
        request_id: str | None = None,
        run_manifest: RunManifestRecord | None = None,
        run_usage: RunCrawlUsageRecord | None = None,
    ) -> CatalogueWriteResult:
        if not self._running:
            raise RuntimeError("repository pipeline is not running")
        if run_manifest is not None:
            await self.queue.submit_manifest(run_manifest)
        return await self.queue.submit(
            crawl,
            request_id=request_id,
            run_usage=run_usage,
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
                await self.submit_stored(
                    exc.crawl,
                    request_id=projection_ingestion_request_id(exc.crawl.document_id),
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
                await self.submit_stored(
                    exc.crawl,
                    request_id=projection_ingestion_request_id(exc.crawl.document_id),
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


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value
