from __future__ import annotations

import asyncio
import os
import unittest
from contextlib import AbstractAsyncContextManager
from unittest.mock import patch
from uuid import uuid4

from actions.crawl.schemas import CrawlPage
from actions.crawl.service import _crawl_concurrency_per_run, crawl, crawl_one_for_task


class CrawlSchedulingTests(unittest.IsolatedAsyncioTestCase):
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

        async def fake_crawl_one_for_task(**kwargs) -> CrawlPage:
            nonlocal active, peak_active
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

        async def no_cached_page(*args, **kwargs):
            return None

        async def loaded_page(*args, **kwargs):
            events.append("crawl")
            return CrawlPage(
                url="https://example.com",
                success=True,
                duration_seconds=0.01,
            )

        with (
            patch("actions.crawl.service.find_crawl_policy_for_url", return_value=None),
            patch("actions.crawl.service.commit_task_checkpoint"),
            patch("actions.crawl.service.capacity_lease", return_value=Lease()),
            patch("actions.crawl.service.AsyncWebCrawler", return_value=Crawler()),
            patch("actions.crawl.service._cached_page", new=no_cached_page),
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


if __name__ == "__main__":
    unittest.main()
