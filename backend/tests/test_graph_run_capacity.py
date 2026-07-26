from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from api.routers.graph_runs import capacity


class GraphRunCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalogue_queue_delivery_states_are_separate(self) -> None:
        jetstream = SimpleNamespace(
            consumer_info=AsyncMock(
                return_value=SimpleNamespace(
                    num_pending=0,
                    num_ack_pending=3,
                    num_redelivered=2,
                )
            ),
            consumers_info=AsyncMock(return_value=[]),
        )
        worker = SimpleNamespace(
            worker_id="ingestion:test:1",
            capability="ingestion",
            configured_capacity=4,
            usable_capacity=4,
            process_ready=True,
            active_operation_count=0,
        )
        runtime = SimpleNamespace(
            workers=object(),
            catalogue_workers=object(),
            jetstream=jetstream,
        )
        session = MagicMock()
        session.scalars.return_value = []

        with (
            patch(
                "api.routers.graph_runs.list_worker_states",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "api.routers.graph_runs.list_catalogue_worker_states",
                new=AsyncMock(return_value=[worker]),
            ),
        ):
            response = await capacity(runtime, session)

        ingestion = next(
            item
            for item in response.catalogue_executors
            if item.capability == "ingestion"
        )
        self.assertEqual(ingestion.backlog, 3)
        self.assertEqual(ingestion.pending, 0)
        self.assertEqual(ingestion.ack_pending, 3)
        self.assertEqual(ingestion.redelivered, 2)
        self.assertEqual(ingestion.waiting_for_redelivery, 3)

    async def test_materialization_queue_excludes_archived_consumers(self) -> None:
        active = "atlas-materialization-active"
        orphan = "atlas-materialization-orphan"

        def consumer(name: str, pending: int, ack_pending: int):
            return SimpleNamespace(
                name=name,
                config=SimpleNamespace(
                    durable_name=name,
                    filter_subject="atlas.catalogue.dml.table",
                ),
                num_pending=pending,
                num_ack_pending=ack_pending,
                num_redelivered=0,
            )

        jetstream = SimpleNamespace(
            consumer_info=AsyncMock(
                return_value=SimpleNamespace(
                    num_pending=0,
                    num_ack_pending=0,
                    num_redelivered=0,
                )
            ),
            consumers_info=AsyncMock(
                return_value=[
                    consumer(active, 2, 1),
                    consumer(orphan, 100, 5),
                ]
            ),
        )
        runtime = SimpleNamespace(
            workers=object(),
            catalogue_workers=object(),
            jetstream=jetstream,
        )
        session = MagicMock()
        session.scalars.return_value = [active]

        with (
            patch(
                "api.routers.graph_runs.list_worker_states",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "api.routers.graph_runs.list_catalogue_worker_states",
                new=AsyncMock(return_value=[]),
            ),
        ):
            response = await capacity(runtime, session)

        materialization = next(
            item
            for item in response.catalogue_executors
            if item.capability == "materialization"
        )
        self.assertEqual(materialization.pending, 2)
        self.assertEqual(materialization.ack_pending, 1)
        self.assertEqual(materialization.backlog, 3)


if __name__ == "__main__":
    unittest.main()
