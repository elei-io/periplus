from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from repository.ingestion.health import HealthMonitor
from runtime.graph_queue import CrawlRequest
from runtime.resource_governor import ResourceCapacityUnavailable
from workers.acquisition import (
    _BufferedCrawl,
    _HostnameDispatchBuffer,
    _PreAcquiredDomainPermit,
    _dispatch_buffered_crawls,
    _keep_buffered_deliveries_alive,
    _release_buffered_deliveries,
    _run_presence_until_stopped,
)


def buffered(hostname: str) -> _BufferedCrawl:
    now = datetime.now(UTC)
    request = CrawlRequest(
        id=uuid4(),
        graph_run_id=uuid4(),
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
        first = buffered("a.example")
        second = buffered("a.example")
        other = buffered("b.example")
        buffer.add(first)
        buffer.add(second)
        buffer.add(other)

        self.assertIs(buffer.pop(), first)
        self.assertIs(buffer.pop(), other)
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

        async def permit(_resource_grants, item):
            if item.hostname == blocked.hostname:
                raise ResourceCapacityUnavailable("domain is full")
            return None

        processor = AsyncMock()
        with (
            patch("workers.acquisition._try_domain_permit", side_effect=permit),
            patch(
                "workers.acquisition._process_dispatched_crawl",
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
                resource_grants=object(),
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
                "workers.acquisition._try_domain_permit",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "workers.acquisition._process_dispatched_crawl",
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
                resource_grants=object(),
                domain_pacing=object(),
                jetstream=object(),
                playwright=object(),
            )
            await asyncio.gather(*active)

        self.assertTrue(launched)
        self.assertEqual(processor.await_count, 3)
        self.assertEqual(len(buffer), 0)

    async def test_buffered_deliveries_are_heartbeated_and_released(self) -> None:
        buffer = _HostnameDispatchBuffer(2)
        item = buffered("a.example")
        buffer.add(item)
        with patch(
            "workers.acquisition.asyncio.sleep",
            new=AsyncMock(side_effect=[None, asyncio.CancelledError]),
        ):
            with self.assertRaises(asyncio.CancelledError):
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


if __name__ == "__main__":
    unittest.main()
