from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
import unittest

from repository.ingestion.health import HealthMonitor
from runtime.catalogue_workers import (
    CatalogueWorkerState,
    catalogue_worker_presence,
    list_catalogue_worker_states,
)


class CatalogueWorkerPresenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_lists_executor_capacity_separately_from_permits(self) -> None:
        states = {
            "ingestion-one": CatalogueWorkerState(
                worker_id="ingestion:one",
                capability="ingestion",
                started_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                capacity=1,
                active_operation_count=1,
                healthy=True,
            ),
            "ingestion-two": CatalogueWorkerState(
                worker_id="ingestion:two",
                capability="ingestion",
                started_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                capacity=1,
                active_operation_count=0,
                healthy=True,
            ),
        }

        class Bucket:
            async def keys(self):
                return list(states)

            async def get(self, key):
                return SimpleNamespace(value=states[key].model_dump_json().encode())

        workers = await list_catalogue_worker_states(Bucket())
        self.assertEqual(len(workers), 2)
        self.assertEqual(sum(worker.capacity for worker in workers), 2)
        self.assertEqual(sum(worker.active_operation_count for worker in workers), 1)

    async def test_presence_marks_health_ready_after_a_publish(self) -> None:
        stop = asyncio.Event()
        monitor = HealthMonitor()
        monitor.dependencies_ready()

        class Bucket:
            async def put(self, _key, _value):
                stop.set()

        await catalogue_worker_presence(
            Bucket(),
            worker_id="ingestion:one",
            capability="ingestion",
            started_at=datetime.now(UTC),
            active_operation_count=lambda: 0,
            healthy=lambda: True,
            stop=stop,
            monitor=monitor,
        )

        self.assertEqual(monitor.status(), (True, "ready"))

    async def test_presence_marks_health_unavailable_after_a_publish_failure(self) -> None:
        stop = asyncio.Event()
        monitor = HealthMonitor()
        monitor.dependencies_ready()

        class Bucket:
            async def put(self, _key, _value):
                stop.set()
                raise PermissionError("KV publish denied")

        await catalogue_worker_presence(
            Bucket(),
            worker_id="materialization:one",
            capability="materialization",
            started_at=datetime.now(UTC),
            active_operation_count=lambda: 0,
            healthy=lambda: True,
            stop=stop,
            monitor=monitor,
        )

        ready, detail = monitor.status()
        self.assertFalse(ready)
        self.assertEqual(
            detail,
            "catalogue_worker_presence: KV publish denied",
        )


if __name__ == "__main__":
    unittest.main()
