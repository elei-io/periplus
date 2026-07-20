from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

from repository.ingestion.health import HealthMonitor
from workers.maintenance import (
    _compaction_sources,
    _open_compaction_consumer,
    _run_operation,
    _wait_for_compaction_trigger,
)


@asynccontextmanager
async def granted(*_args, **_kwargs):
    yield MagicMock()


class MaintenanceWorkerTests(unittest.IsolatedAsyncioTestCase):
    def test_compaction_sources_include_base_and_materialized_tables(self) -> None:
        definition = SimpleNamespace(
            ducklake_table_uuid=SimpleNamespace(hex="1" * 32),
            name="url_features",
        )
        catalogue = unittest.mock.MagicMock()
        catalogue.config.schema = "main"

        with patch(
            "workers.maintenance.active_definitions",
            return_value=[definition],
        ):
            sources = _compaction_sources(catalogue)

        self.assertEqual(
            sources,
            {
                "atlas-compaction-wakeup": "main.crawls",
                "atlas-compaction-urls": "main.urls",
                f"atlas-compact-{'1' * 32}": (
                    "_atlas_materializations.url_features"
                ),
            },
        )

    def test_compaction_consumers_use_ticks_not_typed_changes(self) -> None:
        catalogue = unittest.mock.MagicMock()
        with patch("workers.maintenance.DMLConsumer") as consumer_type:
            consumer = consumer_type.return_value
            consumer.open.return_value = consumer

            opened = _open_compaction_consumer(
                catalogue,
                name="atlas-compaction-urls",
                table="main.urls",
                start_at=42,
            )

        self.assertIs(opened, consumer)
        consumer_type.assert_called_once_with(
            catalogue.lake,
            "atlas-compaction-urls",
            connection=catalogue.connection,
            table="main.urls",
            mode="ticks",
            start_at=42,
            on_exists="use",
            lease_policy="error",
        )

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
