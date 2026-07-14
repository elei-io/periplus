from __future__ import annotations

from contextlib import asynccontextmanager
import unittest
from unittest.mock import MagicMock, patch

from repository.ingestion.health import HealthMonitor
from workers.maintenance import _run_operation


@asynccontextmanager
async def granted(*_args, **_kwargs):
    yield MagicMock()


class MaintenanceWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_operation_runs_behind_resource_and_correctness_fences(self) -> None:
        monitor = HealthMonitor()
        monitor.dependencies_ready()
        monitor.subsystem_ready("maintenance_admission")

        with (
            patch("workers.maintenance.resource_permits", new=granted),
            patch("workers.maintenance.operation_leases", new=granted),
            patch("workers.maintenance.maintenance_lock", return_value=MagicMock()),
            patch("workers.maintenance.compact") as compact,
        ):
            await _run_operation(
                kind="compact",
                operation_lease_store=MagicMock(),
                resource_grants=MagicMock(),
                config=MagicMock(),
                monitor=monitor,
            )

        compact.assert_called_once()
        ready, _detail = monitor.status()
        self.assertTrue(ready)


if __name__ == "__main__":
    unittest.main()
