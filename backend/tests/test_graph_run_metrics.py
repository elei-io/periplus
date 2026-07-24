from __future__ import annotations

import unittest
from types import SimpleNamespace
from uuid import uuid4


from api.routers.graph_runs import (
    _run_stage_counts,
)


class GraphRunStageMetricsTests(unittest.IsolatedAsyncioTestCase):
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
