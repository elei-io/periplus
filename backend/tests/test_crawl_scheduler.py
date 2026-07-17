from __future__ import annotations

from datetime import UTC, datetime
import unittest
from uuid import uuid4

from runtime.crawl_scheduler import scheduled_run_id
from runtime.graph_runs import create_graph_run
from tests.test_graph_runtime import FakeJetStream, FakeKV, policy_snapshot, snapshot


class CrawlSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def test_run_identity_is_stable_per_schedule_occurrence(self) -> None:
        schedule_id = uuid4()
        occurrence = datetime(2026, 7, 17, 6, tzinfo=UTC)

        first = scheduled_run_id(schedule_id, occurrence)
        repeated = scheduled_run_id(schedule_id, occurrence)
        later = scheduled_run_id(
            schedule_id, datetime(2026, 7, 18, 6, tzinfo=UTC)
        )

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, later)

    async def test_repeated_occurrence_admission_reuses_one_graph_run(self) -> None:
        runs = FakeKV()
        requests = FakeKV()
        progress = FakeKV()
        jetstream = FakeJetStream()
        graph = snapshot()
        schedule_id = uuid4()
        run_id = scheduled_run_id(
            schedule_id, datetime(2026, 7, 17, 6, tzinfo=UTC)
        )
        arguments = {
            "runs": runs,
            "requests": requests,
            "progress": progress,
            "jetstream": jetstream,
            "snapshot": graph,
            "urls": ["https://example.com/"],
            "policy_resolver": policy_snapshot,
            "trigger_kind": "schedule",
            "run_id": run_id,
            "trigger_schedule_id": schedule_id,
        }

        first = await create_graph_run(**arguments)
        repeated = await create_graph_run(**arguments)

        self.assertEqual(first.id, run_id)
        self.assertEqual(repeated.id, run_id)
        self.assertEqual(len(runs.values), 1)
        self.assertEqual(repeated.trigger_schedule_id, schedule_id)
        self.assertEqual(repeated.request_count, 1)


if __name__ == "__main__":
    unittest.main()
