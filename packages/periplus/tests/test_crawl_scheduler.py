from __future__ import annotations

from datetime import UTC, datetime
import unittest
from uuid import uuid4

from periplus.crawl.runtime.crawl_scheduler import scheduled_run_id


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

if __name__ == "__main__":
    unittest.main()
