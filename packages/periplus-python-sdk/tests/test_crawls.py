from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from periplus_sdk import crawls
from periplus_sdk.errors import CrawlFailed, WaitTimeout


def _crawl(*, status: crawls.CrawlStatus = "queued") -> crawls.Crawl:
    return crawls.Crawl(
        id=uuid4(),
        urls=("https://example.com/", "https://example.org/"),
        plan_id=uuid4(),
        status=status,
        created_at=datetime.now(UTC),
        _poll_seconds=0.001,
    )


def _payload(crawl: crawls.Crawl, *, status: str) -> dict:
    return {
        "id": str(crawl.id),
        "graph_id": str(crawl.plan_id),
        "trigger_urls": list(crawl.urls),
        "status": status,
        "created_at": crawl.created_at.isoformat(),
    }


class CrawlTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_refreshes_until_terminal(self) -> None:
        crawl = _crawl()
        with patch(
            "periplus_sdk.crawls.request",
            AsyncMock(
                side_effect=[
                    _payload(crawl, status="running"),
                    _payload(crawl, status="completed"),
                ]
            ),
        ):
            result = await crawl.completed(timeout=1)

        self.assertIs(result, crawl)
        self.assertEqual(crawl.status, "completed")

    async def test_timeout_is_local_and_does_not_call_cancel(self) -> None:
        crawl = _crawl()
        request = AsyncMock()
        with patch("periplus_sdk.crawls.request", request):
            with self.assertRaises(WaitTimeout):
                await crawl.completed(timeout=0)

        request.assert_not_awaited()
        self.assertEqual(crawl.status, "queued")

    async def test_task_cancellation_does_not_call_cancel(self) -> None:
        crawl = _crawl()
        crawl._poll_seconds = 60
        request = AsyncMock()
        with patch("periplus_sdk.crawls.request", request):
            waiter = asyncio.create_task(crawl.completed())
            await asyncio.sleep(0)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter

        request.assert_not_awaited()
        self.assertEqual(crawl.status, "queued")

    async def test_completed_with_errors_raises_for_status(self) -> None:
        crawl = _crawl(status="completed_with_errors")

        with self.assertRaises(CrawlFailed):
            crawl.raise_for_status()

    async def test_run_submits_low_depth_crawl(self) -> None:
        crawl = _crawl()
        request = AsyncMock(
            side_effect=[
                {
                    "run_id": str(crawl.id),
                    "plan_id": str(crawl.plan_id),
                    "status": "queued",
                },
                _payload(crawl, status="queued"),
            ]
        )
        with patch("periplus_sdk.crawls.request", request):
            created = await crawls.run(
                crawl.urls,
                depth=0,
                max_crawls=2,
            )

        self.assertEqual(created.id, crawl.id)
        self.assertEqual(
            request.await_args_list[0].kwargs["json"],
            {
                "urls": list(crawl.urls),
                "depth": 0,
                "max_crawls": 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
