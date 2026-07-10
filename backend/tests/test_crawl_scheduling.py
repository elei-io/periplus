from __future__ import annotations

import asyncio
import os
import unittest
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from actions.crawl.schemas import CrawlPage
from actions.crawl.service import (
    _CrawlerPool,
    _crawl_concurrency_per_run,
    _durable_crawl_id,
    _persist_page,
    _quality_warnings_from_crawl,
    _repository_cached_page,
    _repository_retry_page,
    crawl,
    crawl_one_for_task,
)
from actions.shared.cache import ResolvedCachePolicy
from repository.ducklake import CatalogueWriteResult, CrawlRecord
from repository import RepositoryCacheHit


class CrawlSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_no_store_does_not_initialize_repository(self) -> None:
        page = CrawlPage(
            url="https://example.com",
            success=True,
            duration_seconds=0.01,
        )
        session = MagicMock()
        worker_session = MagicMock()

        with (
            patch("actions.crawl.service.commit_task_checkpoint"),
            patch("actions.crawl.service.sessionmaker", return_value=lambda: worker_session),
            patch(
                "actions.crawl.service.repository_ingestor_from_env",
                side_effect=AssertionError("repository initialized"),
            ) as repository_factory,
            patch(
                "actions.crawl.service.crawl_one_for_task",
                new=AsyncMock(return_value=page),
            ),
        ):
            output = await crawl(
                [page.url],
                mode="static",
                wait="none",
                session=session,
                task_run_id=uuid4(),
                cache={"mode": "no_store"},
            )

        self.assertEqual(output.pages, [page])
        repository_factory.assert_not_called()

    async def test_cache_hit_records_usage_for_current_run(self) -> None:
        current_run_id = uuid4()
        crawl_record = CrawlRecord(
            crawl_id=uuid4(),
            document_id="sha256:" + "a" * 64,
            run_id=uuid4(),
            task_id=uuid4(),
            task_revision=1,
            primitive="crawl",
            requested_url="https://example.com",
            normalized_url="https://example.com",
            captured_at=datetime.now(UTC),
            input_json={},
            input_hash="input:v1",
        )
        hit = RepositoryCacheHit(
            crawl=crawl_record,
            document=None,
            html=None,
            links=None,
            projection_rebuilt=False,
            repository_snapshot=4,
        )
        pipeline = MagicMock()
        pipeline.resolve_cached_page = AsyncMock(return_value=hit)

        async def submit_after_checkpoint(*args, **kwargs):
            self.assertTrue(session.commit.called)
            return CatalogueWriteResult(
                document_id=crawl_record.document_id,
                crawl_id=crawl_record.crawl_id,
                document_created=False,
                crawl_created=False,
                repository_snapshot=5,
            )
        session = MagicMock()
        session.get.return_value = SimpleNamespace(
            id=current_run_id,
            task_id=uuid4(),
            primitive="index",
            input_json={"url": "https://example.com"},
            queued_at=datetime.now(UTC),
            task_revision=4,
        )
        pipeline.submit_stored = AsyncMock(side_effect=submit_after_checkpoint)

        page = await _repository_cached_page(
            pipeline,
            session=session,
            task_run_id=current_run_id,
            requested_url="https://example.com",
            normalized_url="https://example.com",
            input_hash="input:v1",
            cache_block_rules=None,
            progress_reporter=None,
            captured_after=None,
            include_html=False,
            include_links=False,
        )

        self.assertIsNotNone(page)
        self.assertEqual(page.repository_snapshot, 5)
        submitted = pipeline.submit_stored.await_args.kwargs
        self.assertEqual(submitted["run_manifest"].run_id, current_run_id)
        self.assertEqual(submitted["run_usage"].crawl_id, crawl_record.crawl_id)
        self.assertEqual(submitted["run_usage"].source, "repository")
        self.assertEqual(submitted["run_usage"].role, "primitive_result")
        self.assertEqual(submitted["run_usage"].ordinal, 0)
        self.assertTrue(submitted["run_usage"].returned)

    async def test_retry_usage_is_submitted_after_postgres_checkpoint(self) -> None:
        run_id = uuid4()
        crawl_record = CrawlRecord(
            crawl_id=uuid4(),
            document_id="sha256:" + "b" * 64,
            run_id=run_id,
            task_id=uuid4(),
            task_revision=2,
            primitive="crawl",
            requested_url="https://example.com",
            normalized_url="https://example.com",
            captured_at=datetime.now(UTC),
            input_json={},
            input_hash="input:v2",
        )
        hit = RepositoryCacheHit(
            crawl=crawl_record,
            document=None,
            html=None,
            links=None,
            projection_rebuilt=False,
            repository_snapshot=8,
        )
        session = MagicMock()
        session.get.return_value = SimpleNamespace(
            id=run_id,
            task_id=crawl_record.task_id,
            primitive="crawl",
            input_json={"url": "https://example.com"},
            queued_at=datetime.now(UTC),
            task_revision=2,
            data_schema_id=None,
        )
        pipeline = MagicMock()
        pipeline.resolve_crawl = AsyncMock(return_value=hit)

        async def submit_after_checkpoint(*args, **kwargs):
            self.assertTrue(session.commit.called)
            return CatalogueWriteResult(
                document_id=crawl_record.document_id,
                crawl_id=crawl_record.crawl_id,
                document_created=False,
                crawl_created=False,
                repository_snapshot=9,
            )

        pipeline.submit_stored = AsyncMock(side_effect=submit_after_checkpoint)

        page = await _repository_retry_page(
            pipeline,
            session=session,
            crawl_id=crawl_record.crawl_id,
            task_run_id=run_id,
            requested_url="https://example.com",
            normalized_url="https://example.com",
            input_hash="input:v2",
            progress_reporter=None,
            include_html=False,
            include_links=False,
        )

        self.assertIsNotNone(page)
        self.assertEqual(page.repository_snapshot, 9)

    def test_cached_quality_warnings_are_rehydrated(self) -> None:
        crawl_record = CrawlRecord(
            crawl_id=uuid4(),
            document_id="sha256:" + "a" * 64,
            run_id=uuid4(),
            task_id=uuid4(),
            task_revision=2,
            primitive="crawl",
            requested_url="https://example.com",
            normalized_url="https://example.com",
            captured_at=datetime.now(UTC),
            input_json={},
            input_hash="input",
            warnings_json=[
                {
                    "code": "too_small",
                    "name": "Small page",
                    "description": "The page is unusually small.",
                    "signals": [],
                }
            ],
        )

        warnings = _quality_warnings_from_crawl(crawl_record)

        self.assertEqual([warning.code for warning in warnings], ["too_small"])

    def test_crawl_identity_is_stable_for_task_run_retries(self) -> None:
        run_id = uuid4()
        values = {
            _durable_crawl_id(
                task_run_id=run_id,
                index=2,
                normalized_url="https://example.com/page",
                input_hash="abc123",
            )
            for _ in range(2)
        }
        self.assertEqual(len(values), 1)
        self.assertNotEqual(
            values.pop(),
            _durable_crawl_id(
                task_run_id=run_id,
                index=3,
                normalized_url="https://example.com/page",
                input_hash="abc123",
            ),
        )

    def test_run_concurrency_defaults_to_three_and_is_bounded(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_crawl_concurrency_per_run(), 3)
        with patch.dict(os.environ, {"ATLAS_CRAWL_CONCURRENCY_PER_RUN": "0"}, clear=True):
            self.assertEqual(_crawl_concurrency_per_run(), 1)
        with patch.dict(os.environ, {"ATLAS_CRAWL_CONCURRENCY_PER_RUN": "invalid"}, clear=True):
            self.assertEqual(_crawl_concurrency_per_run(), 3)

    async def test_batch_uses_bounded_workers_and_preserves_input_order(self) -> None:
        active = 0
        peak_active = 0
        crawler_pools: set[int] = set()

        async def fake_crawl_one_for_task(**kwargs) -> CrawlPage:
            nonlocal active, peak_active
            crawler_pools.add(id(kwargs["crawler_pool"]))
            active += 1
            peak_active = max(peak_active, active)
            try:
                await asyncio.sleep((12 - kwargs["index"]) * 0.001)
                return CrawlPage(
                    url=kwargs["url"],
                    success=True,
                    duration_seconds=0.001,
                )
            finally:
                active -= 1

        urls = [f"https://example.com/{index}" for index in range(12)]
        with (
            patch.dict(os.environ, {"ATLAS_CRAWL_CONCURRENCY_PER_RUN": "2"}),
            patch("actions.crawl.service.crawl_one_for_task", new=fake_crawl_one_for_task),
        ):
            output = await crawl(urls, mode="static", wait="none")

        self.assertEqual(peak_active, 2)
        self.assertEqual(len(crawler_pools), 1)
        self.assertEqual([page.url for page in output.pages], urls)
        self.assertEqual(output.stats.succeeded, len(urls))

    async def test_capacity_is_held_before_browser_opens(self) -> None:
        events: list[str] = []

        class FakeSession:
            pass

        class Lease(AbstractAsyncContextManager[None]):
            async def __aenter__(self) -> None:
                events.append("lease-enter")

            async def __aexit__(self, exc_type, exc, traceback) -> None:
                events.append("lease-exit")

        class Crawler(AbstractAsyncContextManager[object]):
            async def __aenter__(self) -> object:
                events.append("browser-enter")
                return object()

            async def __aexit__(self, exc_type, exc, traceback) -> None:
                events.append("browser-exit")

        async def loaded_page(*args, **kwargs):
            events.append("crawl")
            return CrawlPage(
                url="https://example.com",
                success=True,
                duration_seconds=0.01,
            )

        with (
            patch("actions.crawl.service._frozen_crawl_policy_for_url", return_value=None),
            patch("actions.crawl.service.commit_task_checkpoint"),
            patch("actions.crawl.service.capacity_lease", return_value=Lease()),
            patch("actions.crawl.service.AsyncWebCrawler", return_value=Crawler()),
            patch("actions.crawl.service._crawl_url", new=loaded_page),
            patch("actions.crawl.service._persist_page", side_effect=lambda _session, **kwargs: kwargs["page"]),
        ):
            page = await crawl_one_for_task(
                url="https://example.com",
                mode="static",
                wait="none",
                index=0,
                session=FakeSession(),  # type: ignore[arg-type]
                task_run_id=uuid4(),
            )

        self.assertTrue(page.success)
        self.assertEqual(
            events,
            ["lease-enter", "browser-enter", "crawl", "browser-exit", "lease-exit"],
        )

    async def test_acquisition_exception_duration_is_persisted(self) -> None:
        class Lease(AbstractAsyncContextManager[None]):
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, exc_type, exc, traceback) -> None:
                return None

        session = MagicMock()
        crawler_pool = MagicMock()
        crawler_pool.get = AsyncMock(return_value=object())
        repository_pipeline = MagicMock()
        repository_pipeline.resolve_crawl = AsyncMock(return_value=None)

        with (
            patch("actions.crawl.service._frozen_crawl_policy_for_url", return_value=None),
            patch("actions.crawl.service.commit_task_checkpoint"),
            patch("actions.crawl.service.capacity_lease", return_value=Lease()),
            patch(
                "actions.crawl.service._crawl_url",
                new=AsyncMock(side_effect=RuntimeError("browser failed")),
            ),
            patch(
                "actions.crawl.service.time.perf_counter",
                side_effect=[10.0, 12.5],
            ),
            patch(
                "actions.crawl.service._persist_page",
                new=AsyncMock(side_effect=lambda _session, **kwargs: kwargs["page"]),
            ) as persist,
        ):
            page = await crawl_one_for_task(
                url="https://example.com",
                mode="static",
                wait="none",
                index=0,
                session=session,
                task_run_id=uuid4(),
                crawler_pool=crawler_pool,
                repository_pipeline=repository_pipeline,
                cache={"mode": "refresh"},
            )

        self.assertFalse(page.success)
        self.assertEqual(page.duration_seconds, 2.5)
        self.assertEqual(persist.await_args.kwargs["page"].duration_seconds, 2.5)

    async def test_repository_cache_hit_avoids_capacity_and_browser(self) -> None:
        run_id = uuid4()
        crawl_id = uuid4()
        cached_page = CrawlPage(
            url="https://example.com",
            success=True,
            status_code=200,
            duration_seconds=0.0,
            crawl_id=crawl_id,
            document_id="sha256:" + "a" * 64,
            repository_snapshot=7,
            html="<html></html>",
            crawl={"links": {"internal": [], "external": []}},
        )
        crawler_pool = MagicMock()
        crawler_pool.get = AsyncMock(side_effect=AssertionError("browser requested"))
        session = MagicMock()
        session.get.return_value = None
        repository_pipeline = MagicMock()
        repository_pipeline.resolve_crawl = AsyncMock(return_value=None)

        with (
            patch("actions.crawl.service._frozen_crawl_policy_for_url", return_value=None),
            patch("actions.crawl.service.commit_task_checkpoint"),
            patch(
                "actions.crawl.service._repository_cached_page",
                new=AsyncMock(return_value=cached_page),
            ),
            patch("actions.crawl.service.capacity_lease") as capacity,
        ):
            page = await crawl_one_for_task(
                url="https://example.com",
                mode="static",
                wait="none",
                index=0,
                session=session,
                task_run_id=run_id,
                crawler_pool=crawler_pool,
                repository_pipeline=repository_pipeline,
            )

        self.assertEqual(page.document_id, cached_page.document_id)
        capacity.assert_not_called()
        crawler_pool.get.assert_not_awaited()

    async def test_refresh_bypasses_cache_and_persists_network_result(self) -> None:
        class Lease(AbstractAsyncContextManager[None]):
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, exc_type, exc, traceback) -> None:
                return None

        page = CrawlPage(
            url="https://example.com",
            success=True,
            duration_seconds=0.01,
            html="<html></html>",
        )
        session = MagicMock()
        crawler_pool = MagicMock()
        crawler_pool.get = AsyncMock(return_value=object())
        repository_cache = AsyncMock(side_effect=AssertionError("repository cache read"))
        repository_pipeline = MagicMock()
        repository_pipeline.resolve_crawl = AsyncMock(return_value=None)

        with (
            patch("actions.crawl.service._frozen_crawl_policy_for_url", return_value=None),
            patch("actions.crawl.service.commit_task_checkpoint"),
            patch("actions.crawl.service.capacity_lease", return_value=Lease()),
            patch("actions.crawl.service._repository_cached_page", new=repository_cache),
            patch("actions.crawl.service._crawl_url", new=AsyncMock(return_value=page)),
            patch(
                "actions.crawl.service._persist_page",
                side_effect=lambda _session, **kwargs: kwargs["page"],
            ) as persist,
        ):
            result = await crawl_one_for_task(
                url="https://example.com",
                mode="static",
                wait="none",
                index=0,
                session=session,
                task_run_id=uuid4(),
                crawler_pool=crawler_pool,
                repository_pipeline=repository_pipeline,
                cache={"mode": "refresh"},
            )

        self.assertTrue(result.success)
        repository_cache.assert_not_awaited()
        persist.assert_called_once()

    async def test_retry_resumes_committed_crawl_before_refresh_reacquires(self) -> None:
        run_id = uuid4()
        resumed = CrawlPage(
            url="https://example.com",
            success=True,
            status_code=200,
            duration_seconds=0.01,
            crawl_id=uuid4(),
            document_id="sha256:" + "a" * 64,
            repository_snapshot=9,
            repository_crawl_created=False,
            html="<html>first attempt</html>",
        )
        session = MagicMock()
        crawler_pool = MagicMock()
        crawler_pool.get = AsyncMock(side_effect=AssertionError("browser requested"))

        with (
            patch("actions.crawl.service._frozen_crawl_policy_for_url", return_value=None),
            patch("actions.crawl.service.commit_task_checkpoint"),
            patch(
                "actions.crawl.service._repository_retry_page",
                new=AsyncMock(return_value=resumed),
            ) as retry_lookup,
            patch("actions.crawl.service.capacity_lease") as capacity,
            patch("actions.crawl.service._persist_page") as persist,
        ):
            result = await crawl_one_for_task(
                url="https://example.com",
                mode="static",
                wait="none",
                index=0,
                session=session,
                task_run_id=run_id,
                crawler_pool=crawler_pool,
                repository_pipeline=MagicMock(),
                cache={"mode": "refresh"},
            )

        self.assertEqual(result.crawl_id, resumed.crawl_id)
        retry_lookup.assert_awaited_once()
        capacity.assert_not_called()
        crawler_pool.get.assert_not_awaited()
        persist.assert_not_called()

    async def test_retry_resumed_network_failure_can_fall_back_to_stale_data(self) -> None:
        run_id = uuid4()
        resumed_failure = CrawlPage(
            url="https://example.com",
            success=False,
            duration_seconds=0.01,
            crawl_id=uuid4(),
            error="network unavailable",
        )
        stale = CrawlPage(
            url="https://example.com",
            success=True,
            duration_seconds=0.0,
            crawl_id=uuid4(),
            document_id="sha256:" + "a" * 64,
            repository_snapshot=3,
            html="<html>stale</html>",
            crawl={"cache_status": "stale_if_error", "links": {}},
        )
        session = MagicMock()
        crawler_pool = MagicMock()
        crawler_pool.get = AsyncMock(side_effect=AssertionError("browser requested"))
        stale_lookup = AsyncMock(return_value=stale)

        with (
            patch("actions.crawl.service._frozen_crawl_policy_for_url", return_value=None),
            patch("actions.crawl.service.commit_task_checkpoint"),
            patch(
                "actions.crawl.service._repository_retry_page",
                new=AsyncMock(return_value=resumed_failure),
            ),
            patch(
                "actions.crawl.service._repository_cached_page",
                new=stale_lookup,
            ),
            patch("actions.crawl.service.capacity_lease") as capacity,
            patch("actions.crawl.service._persist_page") as persist,
        ):
            result = await crawl_one_for_task(
                url="https://example.com",
                mode="static",
                wait="none",
                index=0,
                session=session,
                task_run_id=run_id,
                crawler_pool=crawler_pool,
                repository_pipeline=MagicMock(),
                cache={"max_age_seconds": 10, "stale_if_error_seconds": 300},
            )

        self.assertTrue(result.success)
        self.assertEqual(result.document_id, stale.document_id)
        stale_lookup.assert_awaited_once()
        self.assertEqual(stale_lookup.await_args.kwargs["cache_status"], "stale_if_error")
        capacity.assert_not_called()
        crawler_pool.get.assert_not_awaited()
        persist.assert_not_called()

    async def test_persist_rechecks_identity_to_close_concurrent_attempt_race(self) -> None:
        run_id = uuid4()
        resumed = CrawlPage(
            url="https://example.com",
            success=True,
            duration_seconds=0.01,
            crawl_id=uuid4(),
            document_id="sha256:" + "a" * 64,
            repository_snapshot=10,
            repository_crawl_created=False,
            html="<html>first attempt</html>",
        )
        run = SimpleNamespace(
            task_id=uuid4(),
            primitive="crawl",
            input_json={"urls": ["https://example.com"]},
            data_schema_id=None,
            task_revision=3,
        )
        session = MagicMock()
        session.get.return_value = run
        pipeline = MagicMock()
        pipeline.store_raw = AsyncMock()
        pipeline.submit_stored = AsyncMock()

        with (
            patch("actions.crawl.service.commit_task_checkpoint"),
            patch(
                "actions.crawl.service._repository_retry_page",
                new=AsyncMock(return_value=resumed),
            ) as retry_lookup,
        ):
            result = await _persist_page(
                session,
                task_run_id=run_id,
                index=0,
                requested_url="https://example.com",
                page=CrawlPage(
                    url="https://example.com",
                    success=True,
                    duration_seconds=0.02,
                    html="<html>second attempt</html>",
                ),
                mode="static",
                wait="none",
                repository_pipeline=pipeline,
                cache_policy=ResolvedCachePolicy(
                    mode="refresh",
                    max_age_seconds=0,
                ),
                retain_html=True,
                include_links=True,
            )

        self.assertEqual(result.crawl_id, resumed.crawl_id)
        retry_lookup.assert_awaited_once()
        pipeline.store_raw.assert_not_awaited()
        pipeline.submit_stored.assert_not_awaited()

    async def test_no_store_bypasses_cache_and_all_persistence(self) -> None:
        class Lease(AbstractAsyncContextManager[None]):
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, exc_type, exc, traceback) -> None:
                return None

        page = CrawlPage(
            url="https://example.com",
            success=True,
            duration_seconds=0.01,
            html="<html></html>",
        )
        session = MagicMock()
        crawler_pool = MagicMock()
        crawler_pool.get = AsyncMock(return_value=object())
        repository_pipeline = MagicMock()
        repository_pipeline.resolve_crawl = AsyncMock(return_value=None)

        with (
            patch("actions.crawl.service._frozen_crawl_policy_for_url", return_value=None),
            patch("actions.crawl.service.commit_task_checkpoint"),
            patch("actions.crawl.service.capacity_lease", return_value=Lease()),
            patch("actions.crawl.service._repository_cached_page") as repository_cache,
            patch("actions.crawl.service._crawl_url", new=AsyncMock(return_value=page)),
            patch("actions.crawl.service._persist_page") as persist,
        ):
            result = await crawl_one_for_task(
                url="https://example.com",
                mode="static",
                wait="none",
                index=0,
                session=session,
                task_run_id=uuid4(),
                crawler_pool=crawler_pool,
                repository_pipeline=repository_pipeline,
                cache={"mode": "no_store"},
            )

        self.assertTrue(result.success)
        repository_cache.assert_not_called()
        persist.assert_not_called()

    async def test_failed_network_can_fall_back_to_bounded_stale_repository_data(self) -> None:
        class Lease(AbstractAsyncContextManager[None]):
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, exc_type, exc, traceback) -> None:
                return None

        failed = CrawlPage(
            url="https://example.com",
            success=False,
            duration_seconds=0.01,
            error="network unavailable",
        )
        stale = CrawlPage(
            url="https://example.com",
            success=True,
            duration_seconds=0.0,
            crawl_id=uuid4(),
            document_id="sha256:" + "a" * 64,
            repository_snapshot=3,
            html="<html>stale</html>",
            crawl={"cache_status": "stale_if_error", "links": {}},
        )
        session = MagicMock()
        crawler_pool = MagicMock()
        crawler_pool.get = AsyncMock(return_value=object())
        repository_cache = AsyncMock(side_effect=[None, stale])
        repository_pipeline = MagicMock()
        repository_pipeline.resolve_crawl = AsyncMock(return_value=None)

        with (
            patch("actions.crawl.service._frozen_crawl_policy_for_url", return_value=None),
            patch("actions.crawl.service.commit_task_checkpoint"),
            patch("actions.crawl.service.capacity_lease", return_value=Lease()),
            patch("actions.crawl.service._repository_cached_page", new=repository_cache),
            patch("actions.crawl.service._crawl_url", new=AsyncMock(return_value=failed)),
            patch(
                "actions.crawl.service._persist_page",
                side_effect=lambda _session, **kwargs: kwargs["page"],
            ) as persist,
        ):
            result = await crawl_one_for_task(
                url="https://example.com",
                mode="static",
                wait="none",
                index=0,
                session=session,
                task_run_id=uuid4(),
                crawler_pool=crawler_pool,
                repository_pipeline=repository_pipeline,
                cache={"max_age_seconds": 10, "stale_if_error_seconds": 300},
            )

        self.assertEqual(result.document_id, stale.document_id)
        persist.assert_awaited_once()
        self.assertIsNone(persist.await_args.kwargs["page"].html)
        self.assertEqual(repository_cache.await_count, 2)
        stale_call = repository_cache.await_args_list[1].kwargs
        self.assertIsNotNone(stale_call["captured_before"])
        self.assertIsNotNone(stale_call["captured_after"])

    async def test_crawler_pool_reuses_one_crawler_per_mode(self) -> None:
        entered: list[str] = []

        class Crawler(AbstractAsyncContextManager[object]):
            def __init__(self, *, config) -> None:
                self.config = config

            async def __aenter__(self):
                entered.append("crawler")
                return self

            async def __aexit__(self, exc_type, exc, traceback) -> None:
                return None

        with patch("actions.crawl.service.AsyncWebCrawler", new=Crawler):
            async with _CrawlerPool() as pool:
                first, second = await asyncio.gather(
                    pool.get("static"),
                    pool.get("static"),
                )
                dynamic = await pool.get("dynamic")

        self.assertIs(first, second)
        self.assertIsNot(first, dynamic)
        self.assertEqual(entered, ["crawler", "crawler"])

    async def test_shared_crawler_holds_one_global_permit_for_its_lifetime(self) -> None:
        events: list[str] = []

        class Lease(AbstractAsyncContextManager[None]):
            async def __aenter__(self) -> None:
                events.append("permit-enter")

            async def __aexit__(self, exc_type, exc, traceback) -> None:
                events.append("permit-exit")

        class Crawler(AbstractAsyncContextManager[object]):
            def __init__(self, *, config) -> None:
                self.config = config

            async def __aenter__(self):
                events.append("browser-enter")
                return self

            async def __aexit__(self, exc_type, exc, traceback) -> None:
                events.append("browser-exit")

        session = MagicMock()
        run_id = uuid4()
        with (
            patch("actions.crawl.service.capacity_lease", return_value=Lease()) as lease,
            patch("actions.crawl.service.AsyncWebCrawler", new=Crawler),
        ):
            async with _CrawlerPool(session=session, task_run_id=run_id) as pool:
                first, second = await asyncio.gather(
                    pool.get("static", resource_url="https://example.com/1"),
                    pool.get("static", resource_url="https://example.com/2"),
                )

        self.assertIs(first, second)
        lease.assert_called_once_with(
            session,
            task_run_id=run_id,
            url="https://example.com/1",
            policy=None,
            progress_reporter=None,
            include_policy=False,
        )
        self.assertEqual(
            events,
            ["permit-enter", "browser-enter", "browser-exit", "permit-exit"],
        )

if __name__ == "__main__":
    unittest.main()
