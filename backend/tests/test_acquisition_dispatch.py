from __future__ import annotations

import asyncio
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from atlas.crawl.acquisition.errors import PlaywrightRuntimeLost
from atlas.platform.health import HealthMonitor
from atlas.crawl.runtime.domain_pacing import DomainCapacityUnavailable
from atlas.crawl.runtime.graph_queue import CrawlRequest
from atlas.crawl.worker import (
    _BufferedCrawl,
    _dispatch_buffered_crawls,
    _HostnameDispatchBuffer,
    _keep_buffered_deliveries_alive,
    _PreAcquiredDomainPermit,
    _raise_background_failure,
    _release_buffered_deliveries,
    _run_presence_until_stopped,
    _watch_playwright_driver,
)


def buffered(hostname: str, *, run_id=None) -> _BufferedCrawl:
    now = datetime.now(UTC)
    request = CrawlRequest(
        id=uuid4(),
        graph_run_id=run_id or uuid4(),
        node_id=uuid4(),
        url=f"https://{hostname}/",
        effective_policy_snapshot_json={},
        created_at=now,
        updated_at=now,
    )
    return _BufferedCrawl(
        message=SimpleNamespace(
            in_progress=AsyncMock(),
            nak=AsyncMock(),
        ),
        request=request,
        hostname=hostname,
        domain_concurrency=4,
        attempt_number=1,
    )


class AcquisitionDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_robin_preserves_per_hostname_order(self) -> None:
        buffer = _HostnameDispatchBuffer(5)
        run_id = uuid4()
        first = buffered("a.example", run_id=run_id)
        second = buffered("a.example", run_id=run_id)
        other = buffered("b.example", run_id=run_id)
        buffer.add(first)
        buffer.add(second)
        buffer.add(other)

        self.assertIs(buffer.pop(), first)
        self.assertIs(buffer.pop(), other)
        self.assertIs(buffer.pop(), second)

    async def test_round_robin_gives_each_run_a_turn_before_reusing_one(self) -> None:
        buffer = _HostnameDispatchBuffer(5)
        large_run = uuid4()
        first = buffered("a.example", run_id=large_run)
        second = buffered("b.example", run_id=large_run)
        small = buffered("c.example", run_id=uuid4())
        buffer.add(first)
        buffer.add(second)
        buffer.add(small)

        self.assertIs(buffer.pop(), first)
        self.assertIs(buffer.pop(), small)
        self.assertIs(buffer.pop(), second)

    async def test_deferred_hostname_is_skipped_until_its_retry_time(self) -> None:
        buffer = _HostnameDispatchBuffer(3)
        blocked = buffered("blocked.example")
        ready = buffered("ready.example")
        buffer.add(blocked)
        buffer.add(ready)
        buffer.defer_hostname(blocked.hostname, retry_at=10.0)

        self.assertIs(buffer.pop(now=9.0), ready)
        self.assertIsNone(buffer.pop(now=9.0))
        self.assertIs(buffer.pop(now=10.0), blocked)

    async def test_blocked_hostname_does_not_consume_an_execution_lane(self) -> None:
        buffer = _HostnameDispatchBuffer(4)
        blocked = buffered("blocked.example")
        ready = buffered("ready.example")
        buffer.add(blocked)
        buffer.add(ready)
        active: set[asyncio.Task] = set()

        async def permit(_domain_pacing, item):
            if item.hostname == blocked.hostname:
                raise DomainCapacityUnavailable("domain is full")

        processor = AsyncMock()
        with (
            patch(
                "atlas.crawl.worker.domain_backoff_seconds",
                new=AsyncMock(return_value=0),
            ),
            patch("atlas.crawl.worker._try_domain_permit", side_effect=permit),
            patch(
                "atlas.crawl.worker._process_dispatched_crawl",
                processor,
            ),
        ):
            launched = await _dispatch_buffered_crawls(
                buffer,
                active,
                capacity=1,
                runs=object(),
                requests=object(),
                progress=object(),
                repository_pipeline=object(),
                domain_pacing=object(),
                jetstream=object(),
                playwright=object(),
            )
            await asyncio.gather(*active)

        self.assertTrue(launched)
        self.assertEqual(processor.await_args.args[0].hostname, ready.hostname)
        self.assertEqual(len(buffer), 1)
        self.assertIs(buffer.pop(now=float("inf")), blocked)

    async def test_single_hostname_remains_work_conserving(self) -> None:
        buffer = _HostnameDispatchBuffer(4)
        for _ in range(3):
            buffer.add(buffered("only.example"))
        active: set[asyncio.Task] = set()
        processor = AsyncMock()

        with (
            patch(
                "atlas.crawl.worker.domain_backoff_seconds",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "atlas.crawl.worker._try_domain_permit",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "atlas.crawl.worker._process_dispatched_crawl",
                processor,
            ),
        ):
            launched = await _dispatch_buffered_crawls(
                buffer,
                active,
                capacity=3,
                runs=object(),
                requests=object(),
                progress=object(),
                repository_pipeline=object(),
                domain_pacing=object(),
                jetstream=object(),
                playwright=object(),
            )
            await asyncio.gather(*active)

        self.assertTrue(launched)
        self.assertEqual(processor.await_count, 3)
        self.assertEqual(len(buffer), 0)

    async def test_shared_domain_backoff_is_checked_before_the_permit(self) -> None:
        buffer = _HostnameDispatchBuffer(2)
        blocked = buffered("blocked.example")
        ready = buffered("ready.example")
        buffer.add(blocked)
        buffer.add(ready)
        active: set[asyncio.Task] = set()
        permit = AsyncMock(return_value=None)
        processor = AsyncMock()

        async def backoff(_bucket, *, domain):
            return 30 if domain == blocked.hostname else 0

        with (
            patch(
                "atlas.crawl.worker.domain_backoff_seconds",
                side_effect=backoff,
            ),
            patch("atlas.crawl.worker._try_domain_permit", permit),
            patch("atlas.crawl.worker._process_dispatched_crawl", processor),
        ):
            launched = await _dispatch_buffered_crawls(
                buffer,
                active,
                capacity=1,
                runs=object(),
                requests=object(),
                progress=object(),
                repository_pipeline=object(),
                domain_pacing=object(),
                jetstream=object(),
                playwright=object(),
            )
            await asyncio.gather(*active)

        self.assertTrue(launched)
        permit.assert_awaited_once()
        self.assertEqual(permit.await_args.args[1].hostname, ready.hostname)
        self.assertEqual(len(buffer), 1)

    async def test_buffered_deliveries_are_heartbeated_and_released(self) -> None:
        buffer = _HostnameDispatchBuffer(2)
        item = buffered("a.example")
        buffer.add(item)
        with (
            patch(
                "atlas.crawl.worker.asyncio.sleep",
                new=AsyncMock(side_effect=[None, asyncio.CancelledError]),
            ),
            self.assertRaises(asyncio.CancelledError),
        ):
            await _keep_buffered_deliveries_alive(buffer)
        item.message.in_progress.assert_awaited_once()

        await _release_buffered_deliveries(buffer)
        item.message.nak.assert_awaited_once_with()
        self.assertEqual(len(buffer), 0)

    async def test_preacquired_permit_is_released_exactly_once(self) -> None:
        events: list[str] = []

        @asynccontextmanager
        async def permit():
            events.append("acquired")
            try:
                yield "guard"
            finally:
                events.append("released")

        context = permit()
        guard = await context.__aenter__()
        adopted = _PreAcquiredDomainPermit(context, guard)
        async with adopted as value:
            self.assertEqual(value, "guard")
        await adopted.release_if_unused()

        self.assertEqual(events, ["acquired", "released"])

    async def test_presence_recovers_after_a_transient_failure(self) -> None:
        stop = asyncio.Event()
        monitor = HealthMonitor()
        monitor.dependencies_ready()
        attempts = 0

        async def iteration() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("transient NATS timeout")
            stop.set()

        with self.assertLogs(level="ERROR"):
            await _run_presence_until_stopped(
                stop=stop,
                monitor=monitor,
                iteration=iteration,
                interval_seconds=0,
                timeout_seconds=1,
                retry_initial_seconds=0,
            )

        self.assertEqual(attempts, 2)
        self.assertEqual(monitor.status(), (True, "ready"))

    async def test_playwright_driver_exit_is_a_fatal_background_failure(self) -> None:
        driver_failure = asyncio.get_running_loop().create_future()
        driver_failure.set_exception(RuntimeError("driver pipe closed"))
        context = SimpleNamespace(
            _connection=SimpleNamespace(
                _transport=SimpleNamespace(on_error_future=driver_failure)
            )
        )
        task = asyncio.create_task(
            _watch_playwright_driver(context),
            name="acquisition-playwright-driver",
        )
        await asyncio.wait({task})

        with self.assertRaisesRegex(PlaywrightRuntimeLost, "driver process exited"):
            _raise_background_failure((task,))


if __name__ == "__main__":
    unittest.main()
