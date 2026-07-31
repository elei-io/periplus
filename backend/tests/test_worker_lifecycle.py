from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from atlas.platform.health import HealthMonitor
from atlas.platform.process import (
    WorkerEndpointConfig,
    WorkerEndpoints,
    cancel_task,
    monitor_heartbeat,
    run_worker_process,
    supervise_until_stopped,
)


class WorkerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_supervisor_cancels_other_tasks_and_propagates_failure(self) -> None:
        cancelled = asyncio.Event()

        async def failing() -> None:
            raise RuntimeError("worker failed")

        async def waiting() -> None:
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        with self.assertRaisesRegex(RuntimeError, "worker failed"):
            await supervise_until_stopped(
                {"failing": failing(), "waiting": waiting()},
                asyncio.Event(),
            )

        self.assertTrue(cancelled.is_set())

    async def test_supervisor_cancels_tasks_after_stop(self) -> None:
        stop = asyncio.Event()
        cancelled = asyncio.Event()

        async def waiting() -> None:
            try:
                stop.set()
                await asyncio.Future()
            finally:
                cancelled.set()

        await supervise_until_stopped({"waiting": waiting()}, stop)

        self.assertTrue(cancelled.is_set())

    async def test_supervisor_rejects_an_unexpected_successful_exit(self) -> None:
        async def completed() -> None:
            return

        with self.assertRaisesRegex(
            RuntimeError,
            "worker task exited unexpectedly: completed",
        ):
            await supervise_until_stopped(
                {"completed": completed()},
                asyncio.Event(),
            )

    async def test_heartbeat_stops_cooperatively(self) -> None:
        monitor = HealthMonitor(heartbeat_timeout_seconds=0.01)
        monitor.dependencies_ready()
        stop = asyncio.Event()
        task = asyncio.create_task(
            monitor_heartbeat(monitor, stop, interval_seconds=0.001)
        )
        await asyncio.sleep(0.003)
        stop.set()
        await task

        self.assertEqual(monitor.status(), (True, "ready"))

    async def test_cancel_task_reaps_the_task(self) -> None:
        async def waiting() -> None:
            await asyncio.Future()

        task = asyncio.create_task(waiting())

        await cancel_task(task)

        self.assertTrue(task.cancelled())

    async def test_worker_process_owns_endpoints_heartbeat_and_supervision(
        self,
    ) -> None:
        stop = asyncio.Event()
        monitor = HealthMonitor()
        endpoints = MagicMock()
        endpoints.close = unittest.mock.AsyncMock()

        async def work() -> None:
            monitor.dependencies_ready()
            stop.set()

        with (
            patch(
                "atlas.platform.process.WorkerEndpointConfig.from_env",
                return_value=MagicMock(),
            ) as config,
            patch(
                "atlas.platform.process.WorkerEndpoints",
                return_value=endpoints,
            ),
            patch("atlas.platform.process.install_signal_handlers") as signals,
        ):
            await run_worker_process(
                role="janitor",
                monitor=monitor,
                tasks={"work": work()},
                stop=stop,
            )

        config.assert_called_once_with("janitor")
        endpoints.start_health.assert_called_once_with(monitor)
        endpoints.start_metrics.assert_called_once_with()
        signals.assert_called_once_with(stop)
        endpoints.close.assert_awaited_once_with()

    async def test_endpoints_own_and_close_both_servers(self) -> None:
        health_server = MagicMock()
        metrics_server = MagicMock()
        config = WorkerEndpointConfig(
            health_address="127.0.0.1",
            health_port=9001,
            metrics_enabled=True,
            metrics_address="127.0.0.1",
            metrics_port=9002,
        )
        monitor = HealthMonitor()
        with (
            patch(
                "atlas.platform.process.start_health_server",
                return_value=(health_server, MagicMock()),
            ) as start_health,
            patch(
                "atlas.platform.process.start_http_server",
                return_value=(metrics_server, MagicMock()),
            ) as start_metrics,
        ):
            endpoints = WorkerEndpoints(config)
            endpoints.start_health(monitor)
            endpoints.start_metrics()
            await endpoints.close()
            await endpoints.close()

        start_health.assert_called_once_with(
            address="127.0.0.1",
            port=9001,
            monitor=monitor,
        )
        start_metrics.assert_called_once_with(9002, addr="127.0.0.1")
        health_server.shutdown.assert_called_once_with()
        health_server.server_close.assert_called_once_with()
        metrics_server.shutdown.assert_called_once_with()
        metrics_server.server_close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
