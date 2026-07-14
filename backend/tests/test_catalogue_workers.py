from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
import unittest

from runtime.catalogue_workers import (
    CatalogueWorkerState,
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


if __name__ == "__main__":
    unittest.main()
