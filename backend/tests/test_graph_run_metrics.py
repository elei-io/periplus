from __future__ import annotations

import unittest
from types import SimpleNamespace
from uuid import uuid4


from api.routers.graph_runs import (
    _run_stage_counts,
)
from runtime.graph_progress import NodeProgress, node_progress_key


class GraphRunStageMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_requests_are_split_into_fetch_and_downstream_work(self) -> None:
        run_id = uuid4()
        node_id = uuid4()
        progress = NodeProgress(
            graph_run_id=run_id,
            node_id=node_id,
            admitted=10,
            queued=3,
            crawling=2,
            awaiting_navigation=4,
            evaluating_edges=1,
            completed=0,
            failed=0,
            cancelled=0,
            activity=(),
            settled=False,
        )

        class ProgressBucket:
            async def get(self, key: str):
                self_key = node_progress_key(run_id, node_id)
                if key != self_key:
                    raise KeyError(key)
                return SimpleNamespace(value=progress.model_dump_json().encode())

        run = SimpleNamespace(
            id=run_id,
            pending_request_count=10,
            snapshot=SimpleNamespace(nodes=[SimpleNamespace(id=node_id)]),
        )

        counts = await _run_stage_counts(ProgressBucket(), run)

        self.assertEqual(counts, (3, 2, 5))


if __name__ == "__main__":
    unittest.main()
