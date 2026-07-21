from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

from repository.ingestion.health import HealthMonitor
from repository.objects.store import ObjectMetadata
from runtime.catalogue_events import (
    DML_ALL_SUBJECT,
    MAINTENANCE_WAKE_DURABLE,
    CatalogueDMLTick,
    maintenance_wake_consumer_config,
)
from workers.maintenance import (
    _run_operation,
    _run_navigation_cleanup,
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

        requests = []

        @asynccontextmanager
        async def record_request(_bucket, request, **_kwargs):
            requests.append(request)
            yield MagicMock()

        config = MagicMock()
        config.maximum_compaction_pass_bytes = 16 * 1024 * 1024
        with (
            patch("workers.maintenance.resource_permits", new=record_request),
            patch("workers.maintenance.operation_leases", new=granted),
            patch("workers.maintenance.compact") as compact,
            patch(
                "workers.maintenance.ResourceLimits.from_env",
                return_value=SimpleNamespace(object_read=64, object_write=64),
            ),
        ):
            outcome = await _run_operation(
                kind="compact",
                operation_lease_store=MagicMock(),
                resource_grants=MagicMock(),
                config=config,
                monitor=monitor,
            )

        compact.assert_called_once()
        self.assertEqual(len(requests), 1)
        self.assertEqual(
            [(need.name, need.units) for need in requests[0].resources],
            [("catalogue:hot", 1), ("object:read", 2), ("object:write", 2)],
        )
        self.assertEqual(outcome, "idle")
        ready, _detail = monitor.status()
        self.assertTrue(ready)

    async def test_staging_cleanup_does_not_request_shared_permits(self) -> None:
        with (
            patch("workers.maintenance.resource_permits") as permits,
            patch("workers.maintenance.operation_leases", new=granted),
            patch("workers.maintenance.cleanup_staging"),
        ):
            outcome = await _run_operation(
                kind="cleanup",
                operation_lease_store=MagicMock(),
                resource_grants=MagicMock(),
                config=MagicMock(),
            )

        self.assertEqual(outcome, "idle")
        permits.assert_not_called()

    async def test_navigation_cleanup_deletes_only_terminal_and_orphan_runs(self) -> None:
        now = datetime(2026, 7, 21, tzinfo=UTC)
        terminal_id, active_id, orphan_id = uuid4(), uuid4(), uuid4()
        keys = {
            terminal_id: f"runtime/navigation/{terminal_id.hex}/documents/a/p.arrow",
            active_id: f"runtime/navigation/{active_id.hex}/documents/b/p.arrow",
            orphan_id: f"runtime/navigation/{orphan_id.hex}/documents/c/p.arrow",
        }
        store = MagicMock()
        store.list_objects.return_value = tuple(
            ObjectMetadata(
                key=key,
                size=1,
                last_modified=now - timedelta(seconds=61),
            )
            for key in keys.values()
        )
        store.delete_many.return_value = 2

        async def graph_run(_runs, run_id):
            if run_id == terminal_id:
                return SimpleNamespace(status="completed")
            if run_id == active_id:
                return SimpleNamespace(status="running")
            return None

        requests = []

        @asynccontextmanager
        async def record_request(_bucket, request, **_kwargs):
            requests.append(request)
            yield MagicMock()

        with (
            patch("workers.maintenance.resource_permits", new=record_request),
            patch("workers.maintenance.operation_leases", new=granted),
            patch("workers.maintenance.get_graph_run", side_effect=graph_run),
            patch("workers.maintenance.get_int", return_value=60),
        ):
            outcome = await _run_navigation_cleanup(
                runs=object(),
                store=store,
                operation_lease_store=object(),
                resource_grants=object(),
                now=now,
            )

        self.assertEqual(outcome, "worked")
        self.assertEqual(
            [(need.name, need.units) for need in requests[0].resources],
            [("object:read", 1), ("object:write", 1)],
        )
        store.delete_many.assert_called_once_with(
            (keys[terminal_id], keys[orphan_id])
        )

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
