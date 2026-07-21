from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

from repository.ingestion.health import HealthMonitor
from runtime.catalogue_events import (
    DML_ALL_SUBJECT,
    MAINTENANCE_WAKE_DURABLE,
    CatalogueDMLTick,
    maintenance_wake_consumer_config,
)
from workers.maintenance import (
    _run_operation,
    _wait_for_compaction_trigger,
    _watch_compaction_ticks,
)


@asynccontextmanager
async def granted(*_args, **_kwargs):
    yield MagicMock()


class MaintenanceWorkerTests(unittest.IsolatedAsyncioTestCase):
    def test_compaction_uses_one_durable_catalogue_tick_subscription(self) -> None:
        config = maintenance_wake_consumer_config()

        self.assertEqual(config.durable_name, MAINTENANCE_WAKE_DURABLE)
        self.assertEqual(config.filter_subject, DML_ALL_SUBJECT)
        self.assertEqual(config.max_ack_pending, 1000)

    async def test_catalogue_ticks_are_acked_after_waking_maintenance(self) -> None:
        stop = asyncio.Event()
        wake = asyncio.Event()
        message = MagicMock()
        message.data = CatalogueDMLTick(
            table_id=1,
            table_uuid=uuid4(),
            schema_name="main",
            table_name="crawls",
            snapshot_id=42,
            snapshot_time=None,
            schema_version=1,
        ).model_dump_json().encode()
        message.ack = AsyncMock()

        class Subscription:
            async def fetch(self, **_kwargs):
                stop.set()
                return [message]

        await _watch_compaction_ticks(stop, wake, Subscription())

        self.assertTrue(wake.is_set())
        message.ack.assert_awaited_once_with()

    async def test_idle_catalogue_tick_subscription_remains_healthy(self) -> None:
        stop = asyncio.Event()
        wake = asyncio.Event()
        monitor = MagicMock()

        class Subscription:
            calls = 0

            async def fetch(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise asyncio.TimeoutError
                stop.set()
                return []

        subscription = Subscription()
        await _watch_compaction_ticks(stop, wake, subscription, monitor)

        self.assertEqual(subscription.calls, 2)
        self.assertFalse(wake.is_set())
        monitor.subsystem_unavailable.assert_not_called()

    async def test_operation_runs_behind_resource_and_operation_admission(self) -> None:
        monitor = HealthMonitor()
        monitor.dependencies_ready()
        monitor.subsystem_ready("maintenance_admission")

        with (
            patch("workers.maintenance.resource_permits", new=granted),
            patch("workers.maintenance.operation_leases", new=granted),
            patch("workers.maintenance.compact") as compact,
        ):
            outcome = await _run_operation(
                kind="compact",
                operation_lease_store=MagicMock(),
                resource_grants=MagicMock(),
                config=MagicMock(),
                monitor=monitor,
            )

        compact.assert_called_once()
        self.assertEqual(outcome, "idle")
        ready, _detail = monitor.status()
        self.assertTrue(ready)

    async def test_compaction_trigger_debounces_repeated_cdc_activity(self) -> None:
        stop = asyncio.Event()
        wake = asyncio.Event()
        wait_for_signal = AsyncMock(
            side_effect=["wake", "wake", "timeout"]
        )

        with patch(
            "workers.maintenance._wait_for_signal",
            wait_for_signal,
        ):
            trigger = await _wait_for_compaction_trigger(
                stop,
                wake,
                fallback_seconds=300,
                debounce_seconds=15,
                maximum_delay_seconds=120,
            )

        self.assertEqual(trigger, "event")
        self.assertEqual(
            wait_for_signal.call_args_list,
            [
                call(stop, wake, timeout=300),
                call(stop, wake, timeout=15),
                call(stop, wake, timeout=15),
            ],
        )

    async def test_compaction_trigger_retains_periodic_fallback(self) -> None:
        stop = asyncio.Event()
        wake = asyncio.Event()
        with patch(
            "workers.maintenance._wait_for_signal",
            AsyncMock(return_value="timeout"),
        ):
            trigger = await _wait_for_compaction_trigger(
                stop,
                wake,
                fallback_seconds=300,
                debounce_seconds=15,
                maximum_delay_seconds=120,
            )

        self.assertEqual(trigger, "periodic")


if __name__ == "__main__":
    unittest.main()
