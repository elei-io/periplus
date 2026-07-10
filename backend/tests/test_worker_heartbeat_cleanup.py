from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from tasks.heartbeats import (
    cleanup_expired_worker_heartbeats,
    purge_stale_worker_heartbeats,
    worker_heartbeat_cleanup_interval_seconds,
    worker_heartbeat_retention_seconds,
    worker_heartbeat_stale_after_seconds,
)


class _DeleteResult:
    rowcount = 3


class _RecordingSession:
    def __init__(self) -> None:
        self.statement = None

    def execute(self, statement):
        self.statement = statement
        return _DeleteResult()


class WorkerHeartbeatCleanupTests(unittest.TestCase):
    def test_cleanup_deletes_rows_older_than_retention(self) -> None:
        now = datetime(2026, 7, 10, 12, tzinfo=UTC)
        session = _RecordingSession()

        deleted = cleanup_expired_worker_heartbeats(
            session,  # type: ignore[arg-type]
            now=now,
            retention_seconds=3600,
        )

        self.assertEqual(deleted, 3)
        assert session.statement is not None
        parameters = session.statement.compile().params
        self.assertIn(now - timedelta(hours=1), parameters.values())

    def test_cleanup_configuration_has_bounded_defaults_and_overrides(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(worker_heartbeat_retention_seconds(), 86400)
            self.assertEqual(worker_heartbeat_cleanup_interval_seconds(), 3600)

        with patch.dict(
            "os.environ",
            {
                "ATLAS_WORKER_HEARTBEAT_RETENTION_SECONDS": "7200",
                "ATLAS_WORKER_HEARTBEAT_CLEANUP_INTERVAL_SECONDS": "300",
            },
            clear=True,
        ):
            self.assertEqual(worker_heartbeat_retention_seconds(), 7200)
            self.assertEqual(worker_heartbeat_cleanup_interval_seconds(), 300)

    def test_purge_removes_stale_and_stopped_workers(self) -> None:
        now = datetime(2026, 7, 10, 12, tzinfo=UTC)
        session = _RecordingSession()

        deleted = purge_stale_worker_heartbeats(
            session,  # type: ignore[arg-type]
            now=now,
            stale_after_seconds=20,
        )

        self.assertEqual(deleted, 3)
        assert session.statement is not None
        statement = str(session.statement)
        self.assertIn("worker_heartbeats.stopping IS true", statement)
        self.assertIn("worker_heartbeats.last_seen_at <", statement)
        self.assertIn(now - timedelta(seconds=20), session.statement.compile().params.values())

    def test_stale_threshold_is_two_heartbeat_intervals(self) -> None:
        with patch.dict(
            "os.environ", {"ATLAS_WORKER_HEARTBEAT_SECONDS": "7.5"}, clear=True
        ):
            self.assertEqual(worker_heartbeat_stale_after_seconds(), 15)


if __name__ == "__main__":
    unittest.main()
