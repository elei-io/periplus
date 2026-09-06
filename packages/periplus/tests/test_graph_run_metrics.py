from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4


from periplus.crawl.api.runs import (
    _run_stage_counts,
    capacity,
)


class GraphRunStageMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_reports_only_acquisition_workers(self) -> None:
        workers = [
            SimpleNamespace(
                worker_id="acquisition-one",
                capacity=12,
                active_request_count=3,
                last_seen_at=datetime.now(UTC),
            )
        ]
        runtime = SimpleNamespace(
            workers=object(),
            jetstream=SimpleNamespace(
                consumer_info=AsyncMock(
                    side_effect=AssertionError(
                        "crawl capacity must not inspect data queues"
                    )
                )
            ),
            catalogue_workers=object(),
        )

        with patch(
            "periplus.crawl.api.runs.list_worker_states",
            AsyncMock(return_value=workers),
        ):
            result = await capacity(runtime)

        self.assertEqual(result.worker_count, 1)
        self.assertEqual(result.runtime_capacity, 12)
        self.assertEqual(result.runtime_active, 3)
        self.assertEqual(len(result.workers), 1)
        self.assertNotIn("catalogue_executors", type(result).model_fields)
        self.assertNotIn("tuning", type(result).model_fields)

    async def test_pending_requests_are_split_into_acquisition_and_navigation(self) -> None:
        run_id = uuid4()
        node_id = uuid4()
        class ProgressStore:
            async def progress_counts(self, requested_run_id):
                assert requested_run_id == run_id
                return {
                    (node_id, "queued"): 3,
                    (node_id, "crawling"): 2,
                    (node_id, "awaiting_navigation"): 4,
                    (node_id, "evaluating_edges"): 1,
                }

        run = SimpleNamespace(
            id=run_id,
            pending_request_count=10,
            snapshot=SimpleNamespace(nodes=[SimpleNamespace(id=node_id)]),
        )

        counts = await _run_stage_counts(ProgressStore(), run)

        self.assertEqual(counts, (3, 2, 5))


if __name__ == "__main__":
    unittest.main()
