from __future__ import annotations

import unittest
from datetime import UTC, datetime
from uuid import UUID

from repository.ducklake import CatalogueWriteResult, CrawlRecord
from repository.pipeline import RepositoryPipeline
from repository.service import ProjectionRebuildRequired


def _crawl() -> CrawlRecord:
    return CrawlRecord(
        crawl_id=UUID(int=1),
        document_id="sha256:" + "a" * 64,
        run_id=UUID(int=2),
        task_id=UUID(int=3),
        task_revision=1,
        primitive="crawl",
        requested_url="https://example.com",
        normalized_url="https://example.com",
        final_url="https://example.com",
        captured_at=datetime(2026, 7, 11, tzinfo=UTC),
        status_code=200,
        duration_ms=1,
        input_json={},
        input_hash="input:v1",
    )


class _FakeQueue:
    def __init__(self) -> None:
        self.submitted = []

    async def connect(self) -> None:
        pass

    async def submit(
        self,
        crawl,
        *,
        request_id=None,
        run_manifest=None,
        run_usage=None,
    ):
        self.submitted.append(crawl)
        return CatalogueWriteResult(
            document_id=crawl.document_id,
            crawl_id=crawl.crawl_id,
            document_created=False,
            crawl_created=False,
            repository_snapshot=2,
        )

    async def resume(self, _crawl_id):
        return None

    async def close(self) -> None:
        pass


class _FakeIngestor:
    def __init__(self, crawl: CrawlRecord) -> None:
        self.crawl = crawl
        self.reads = 0

    def validate(self) -> None:
        pass

    def close(self) -> None:
        pass

    def resolve_cached_page(self, **_kwargs):
        self.reads += 1
        if self.reads == 1:
            raise ProjectionRebuildRequired(self.crawl)
        return "cache-hit"

    def resolve_crawl(self, crawl_id, **_kwargs):
        if crawl_id != self.crawl.crawl_id:
            return None
        return "retry-hit"


class _PendingIngestor(_FakeIngestor):
    def __init__(self, crawl: CrawlRecord) -> None:
        super().__init__(crawl)
        self.retry_reads = 0

    def resolve_crawl(self, crawl_id, **_kwargs):
        self.retry_reads += 1
        if self.retry_reads == 1:
            return None
        return super().resolve_crawl(crawl_id)


class _ResumingQueue(_FakeQueue):
    def __init__(self) -> None:
        super().__init__()
        self.resumed = []

    async def resume(self, crawl_id):
        self.resumed.append(crawl_id)
        return CatalogueWriteResult(
            document_id="sha256:" + "a" * 64,
            crawl_id=crawl_id,
            document_created=False,
            crawl_created=True,
            repository_snapshot=2,
        )


class RepositoryPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_crawl_identity_returns_miss_without_republishing(self) -> None:
        crawl = _crawl()
        queue = _FakeQueue()
        ingestor = _FakeIngestor(crawl)
        async with RepositoryPipeline(ingestor, queue=queue) as pipeline:  # type: ignore[arg-type]
            result = await pipeline.resolve_crawl(UUID(int=99))

        self.assertIsNone(result)
        self.assertEqual(queue.submitted, [])

    async def test_pending_stable_ingestion_is_resumed_before_returning_crawl(self) -> None:
        crawl = _crawl()
        queue = _ResumingQueue()
        ingestor = _PendingIngestor(crawl)
        async with RepositoryPipeline(ingestor, queue=queue) as pipeline:  # type: ignore[arg-type]
            result = await pipeline.resolve_crawl(crawl.crawl_id)

        self.assertEqual(result, "retry-hit")
        self.assertEqual(queue.resumed, [crawl.crawl_id])
        self.assertEqual(ingestor.retry_reads, 2)

    async def test_retry_stable_crawl_is_resolved_through_read_boundary(self) -> None:
        crawl = _crawl()
        queue = _FakeQueue()
        ingestor = _FakeIngestor(crawl)
        async with RepositoryPipeline(ingestor, queue=queue) as pipeline:  # type: ignore[arg-type]
            result = await pipeline.resolve_crawl(crawl.crawl_id)

        self.assertEqual(result, "retry-hit")
        self.assertEqual(queue.submitted, [])

    async def test_stale_projection_is_repaired_by_dedicated_queue_then_reread(self) -> None:
        crawl = _crawl()
        queue = _FakeQueue()
        ingestor = _FakeIngestor(crawl)
        async with RepositoryPipeline(ingestor, queue=queue) as pipeline:  # type: ignore[arg-type]
            result = await pipeline.resolve_cached_page(normalized_url=crawl.normalized_url)

        self.assertEqual(result, "cache-hit")
        self.assertEqual(queue.submitted, [crawl])
        self.assertEqual(ingestor.reads, 2)


if __name__ == "__main__":
    unittest.main()
