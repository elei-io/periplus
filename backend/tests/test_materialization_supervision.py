from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from ducklake_cdc_client import LeaseContentionError

from materialization.executor import (
    _probe_dependencies_once,
    _supervise_cdc,
    _supervise_subsystem,
)
from materialization.live import (
    _close_consumer,
    _open_crawl_planner_consumer,
    _run_blocking,
    run_crawl_planner,
)
from repository.ingestion.health import HealthMonitor


class MaterializationSupervisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_dependency_probe_treats_an_owned_catalogue_lane_as_busy(self) -> None:
        client = SimpleNamespace(flush=AsyncMock())
        catalogue = SimpleNamespace(connection=SimpleNamespace(execute=MagicMock()))
        lane = SimpleNamespace(locked=lambda: True)
        with (
            patch(
                "materialization.executor.resource_permits",
                side_effect=AssertionError("health must not request a work permit"),
            ),
            patch("materialization.executor.catalogue_operation_lane", return_value=lane),
            patch(
                "materialization.executor.run_catalogue_operation",
                new=AsyncMock(),
            ) as run_operation,
        ):
            await _probe_dependencies_once(
                client,
                catalogue,
                timeout=0.1,
            )

        client.flush.assert_awaited_once()
        run_operation.assert_not_awaited()

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

    async def test_subsystem_failure_is_retried_without_escaping_supervisor(
        self,
    ) -> None:
        stop = asyncio.Event()
        attempts = 0
        retried = asyncio.Event()

        async def operation() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("DuckLake scan failed")
            retried.set()
            await stop.wait()

        monitor = HealthMonitor()
        monitor.dependencies_ready()
        task = asyncio.create_task(
            _supervise_subsystem("backfill", operation, stop, monitor)
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

    def test_normal_close_releases_consumer(self) -> None:
        catalogue = MagicMock()
        consumer = MagicMock()
        consumer.name = "consumer"

        _close_consumer(catalogue, consumer, drop=False)

        consumer.close.assert_called_once_with(timeout=5.0, cancel=False, release=True)
        consumer.client.cdc_consumer_force_release.assert_not_called()

    def test_crawl_planner_consumer_owns_a_dedicated_connection(self) -> None:
        catalogue = SimpleNamespace(
            lake=MagicMock(),
            config=SimpleNamespace(schema="main"),
        )
        with patch("materialization.live.DMLConsumer") as consumer_type:
            consumer = consumer_type.return_value
            consumer.open.return_value = consumer

            opened = _open_crawl_planner_consumer(catalogue, 42, "use")

        self.assertIs(opened, consumer)
        consumer_type.assert_called_once_with(
            catalogue.lake,
            "atlas-crawl-materialization-planner",
            table="main.crawls",
            mode="changes",
            start_at=42,
            on_exists="use",
            lease_policy="error",
        )
        consumer.open.assert_called_once_with()

    async def test_crawl_planner_stays_idle_without_live_definitions(self) -> None:
        stop = asyncio.Event()

        async def stop_after_idle(*_args) -> None:
            stop.set()

        monitor = HealthMonitor()
        monitor.dependencies_ready()
        with (
            patch("materialization.live.active_definitions", return_value=[]),
            patch("materialization.live._wait", new=stop_after_idle),
            patch(
                "materialization.live._run_active_crawl_planner",
                new=AsyncMock(),
            ) as run_active,
        ):
            await run_crawl_planner(MagicMock(), stop, monitor)

        run_active.assert_not_awaited()
        self.assertEqual(monitor.status(), (True, "ready"))


if __name__ == "__main__":
    unittest.main()
