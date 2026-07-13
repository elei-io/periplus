from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from nats.js.errors import KeyNotFoundError

from repository.ingestion.health import HealthMonitor
from workers.maintenance import _run_operation


class MaintenanceWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_lost_lease_marks_worker_unready_after_operation_drains(self) -> None:
        leases = MagicMock()
        leases.create = AsyncMock(return_value=1)
        leases.update = AsyncMock(side_effect=RuntimeError("NATS unavailable"))
        leases.get = AsyncMock(side_effect=KeyNotFoundError)
        monitor = HealthMonitor()
        monitor.dependencies_ready()
        monitor.subsystem_ready("maintenance_lease")

        with (
            patch("workers.maintenance.get_float", return_value=0.01),
            patch("workers.maintenance.maintenance_lock", return_value=MagicMock()),
            patch(
                "workers.maintenance.compact",
                side_effect=lambda _config: time.sleep(0.05),
            ),
        ):
            await asyncio.wait_for(
                _run_operation(
                    kind="compact",
                    worker_id="worker-1",
                    leases=leases,
                    config=MagicMock(),
                    monitor=monitor,
                ),
                timeout=1,
            )

        ready, detail = monitor.status()
        self.assertFalse(ready)
        self.assertIn("NATS unavailable", detail)


if __name__ == "__main__":
    unittest.main()
