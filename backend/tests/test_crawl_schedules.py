from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from control.crawl_graphs.models import CrawlGraph, CrawlGraphEdge, CrawlGraphNode
from control.crawl_graphs.schemas import CrawlGraphCreate, CrawlGraphNodeCreate
from control.crawl_graphs.service import create_graph, create_node
from control.crawl_schedules.models import CrawlSchedule
from control.crawl_schedules.schemas import (
    CronTiming,
    CrawlScheduleCreate,
    IntervalTiming,
    SchedulePreviewRequest,
)
from control.crawl_schedules.service import (
    CrawlScheduleValidationError,
    create_schedule,
    preview_occurrences,
    set_schedule_enabled,
)
from db import Base


class CrawlScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            self.engine,
            tables=[
                CrawlGraph.__table__,
                CrawlGraphNode.__table__,
                CrawlGraphEdge.__table__,
                CrawlSchedule.__table__,
            ],
        )
        self.session = Session(self.engine, expire_on_commit=False)
        graph = create_graph(
            self.session, CrawlGraphCreate(slug="scheduled")
        )
        create_node(
            self.session,
            graph.id,
            CrawlGraphNodeCreate(name="root"),
        )
        self.graph_id = graph.id

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_interval_schedule_normalizes_and_deduplicates_urls(self) -> None:
        now = datetime(2026, 7, 17, 12, tzinfo=UTC)
        schedule = create_schedule(
            self.session,
            self.graph_id,
            CrawlScheduleCreate(
                name="Hourly",
                timing=IntervalTiming(kind="interval", seconds=3600),
                max_crawls=250,
                root_urls=[
                    "HTTPS://Example.com",
                    "https://example.com/",
                ],
            ),
            now=now,
        )

        self.assertEqual(schedule.root_urls, ["https://example.com/"])
        self.assertEqual(schedule.max_crawls, 250)
        self.assertEqual(schedule.next_run_at, now + timedelta(hours=1))
        self.assertEqual(schedule.status, "active")

    def test_future_start_is_the_first_interval_occurrence(self) -> None:
        now = datetime(2026, 7, 17, 12, tzinfo=UTC)
        starts_at = now + timedelta(days=1)
        schedule = create_schedule(
            self.session,
            self.graph_id,
            CrawlScheduleCreate(
                name="Windowed",
                timing=IntervalTiming(kind="interval", seconds=3600),
                starts_at=starts_at,
                root_urls=["https://example.com/"],
            ),
            now=now,
        )

        self.assertEqual(schedule.next_run_at, starts_at)
        self.assertEqual(schedule.status, "not_started")

    def test_paused_schedule_has_no_next_occurrence(self) -> None:
        schedule = create_schedule(
            self.session,
            self.graph_id,
            CrawlScheduleCreate(
                name="Paused",
                enabled=False,
                timing=IntervalTiming(kind="interval", seconds=3600),
                root_urls=["https://example.com/"],
            ),
        )

        self.assertIsNone(schedule.next_run_at)
        self.assertEqual(schedule.status, "paused")

    def test_cron_preview_uses_the_requested_timezone(self) -> None:
        preview = preview_occurrences(
            SchedulePreviewRequest(
                timing=CronTiming(
                    kind="cron",
                    expression="0 6 * * *",
                    timezone="Asia/Tokyo",
                ),
                count=2,
            ),
            now=datetime(2026, 7, 17, 0, tzinfo=UTC),
        )

        self.assertEqual(
            preview,
            [
                datetime(2026, 7, 17, 21, tzinfo=UTC),
                datetime(2026, 7, 18, 21, tzinfo=UTC),
            ],
        )

    def test_invalid_timezone_is_rejected(self) -> None:
        with self.assertRaises(CrawlScheduleValidationError):
            preview_occurrences(
                SchedulePreviewRequest(
                    timing=CronTiming(
                        kind="cron",
                        expression="0 6 * * *",
                        timezone="Not/AZone",
                    )
                )
            )

    def test_exhausted_schedule_cannot_be_resumed(self) -> None:
        schedule = create_schedule(
            self.session,
            self.graph_id,
            CrawlScheduleCreate(
                name="Once",
                timing=IntervalTiming(kind="interval", seconds=60),
                maximum_run_count=1,
                root_urls=["https://example.com/"],
            ),
        )
        model = self.session.get(CrawlSchedule, schedule.id)
        assert model is not None
        model.run_count = 1
        model.enabled = False
        self.session.flush()

        resumed = set_schedule_enabled(
            self.session, self.graph_id, schedule.id, True
        )

        self.assertEqual(resumed.status, "exhausted")
        self.assertIsNone(resumed.next_run_at)


if __name__ == "__main__":
    unittest.main()
