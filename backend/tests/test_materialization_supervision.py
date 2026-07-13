from __future__ import annotations

import asyncio
import threading
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from ducklake_cdc_client import LeaseContentionError

from materialization.executor import (
    _maintenance_active as evaluator_maintenance_active,
    _supervise_cdc,
    _supervise_subsystem,
)
from materialization.live import (
    _close_consumer,
    _reconcile_unplanned_crawls,
    _run_blocking,
)
from repository.ingestion.health import HealthMonitor
from materialization.writer import (
    _maintenance_active as writer_maintenance_active,
    _raise_if_subsystem_task_exited,
    _report_subsystem_task_exit,
)


class MaterializationSupervisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_cdc_failure_is_retried_without_escaping_supervisor(self) -> None:
        stop = asyncio.Event()
        attempts = 0
        retried = asyncio.Event()

        async def operation() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise LeaseContentionError("lease contention")
            retried.set()
            await stop.wait()

        monitor = HealthMonitor()
        monitor.dependencies_ready()
        task = asyncio.create_task(
            _supervise_cdc("cdc_test", operation, stop, monitor)
        )
        await asyncio.wait_for(retried.wait(), timeout=2)
        self.assertFalse(task.done())
        stop.set()
        await asyncio.wait_for(task, timeout=1)
        self.assertEqual(attempts, 2)

    async def test_readiness_failure_is_retried_without_escaping_supervisor(self) -> None:
        stop = asyncio.Event()
        attempts = 0
        retried = asyncio.Event()

        async def readiness() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("DuckLake scan failed")
            retried.set()
            await stop.wait()

        monitor = HealthMonitor()
        monitor.dependencies_ready()
        task = asyncio.create_task(
            _supervise_subsystem(
                "readiness_reconciliation", readiness, stop, monitor
            )
        )
        await asyncio.wait_for(retried.wait(), timeout=2)
        self.assertFalse(task.done())
        stop.set()
        await asyncio.wait_for(task, timeout=1)

    async def test_blocking_call_finishes_before_cancellation_returns(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def blocking() -> None:
            started.set()
            release.wait(timeout=2)

        task = asyncio.create_task(_run_blocking(blocking))
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task

    def test_normal_close_does_not_force_release(self) -> None:
        catalogue = MagicMock()
        consumer = MagicMock()
        consumer.name = "consumer"

        _close_consumer(catalogue, consumer, drop=False)

        consumer.close.assert_called_once_with(
            timeout=5.0, cancel=False, release=True
        )
        consumer.client.cdc_consumer_force_release.assert_not_called()

    async def test_failed_background_consumer_marks_health_and_is_raised(self) -> None:
        async def fail() -> None:
            raise OSError("MinIO unavailable")

        monitor = HealthMonitor()
        monitor.dependencies_ready()
        monitor.subsystem_ready("materialization_commit")
        task = asyncio.create_task(fail())
        await asyncio.sleep(0)

        _report_subsystem_task_exit(task, monitor, "materialization_commit")

        ready, detail = monitor.status()
        self.assertFalse(ready)
        self.assertIn("MinIO unavailable", detail)
        with self.assertRaisesRegex(RuntimeError, "materialization commit consumer failed"):
            _raise_if_subsystem_task_exited(
                task, "materialization commit consumer"
            )

    async def test_reconciliation_pages_each_missing_crawl_once(self) -> None:
        now = datetime.now(UTC)
        first_page = [
            (uuid4(), f"sha256:{index}", now + timedelta(microseconds=index))
            for index in range(100)
        ]
        second_page = [(uuid4(), "sha256:last", now + timedelta(seconds=1))]
        jetstream = MagicMock()
        jetstream.publish = AsyncMock()

        with (
            patch("materialization.live.active_definitions", return_value=[]),
            patch(
                "materialization.live._run_blocking",
                new=AsyncMock(side_effect=[first_page, second_page]),
            ),
        ):
            count = await _reconcile_unplanned_crawls(jetstream)

        self.assertEqual(count, 101)
        identifiers = [
            call.kwargs["headers"]["Nats-Msg-Id"]
            for call in jetstream.publish.await_args_list
        ]
        self.assertEqual(len(identifiers), len(set(identifiers)))

    async def test_transient_maintenance_lease_failure_pauses_work(self) -> None:
        bucket = MagicMock()
        bucket.get = AsyncMock(side_effect=TimeoutError("NATS unavailable"))

        self.assertTrue(await evaluator_maintenance_active(bucket))
        self.assertTrue(await writer_maintenance_active(bucket))
