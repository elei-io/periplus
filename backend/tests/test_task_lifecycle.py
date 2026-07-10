from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

from tasks.executor import _renew_lease, recover_expired_runs
from tasks.models import TaskRun, TaskRunLease
from tasks.service import request_task_run_cancellation


def _run(status: str) -> TaskRun:
    now = datetime.now(UTC)
    return TaskRun(
        id=uuid4(),
        task_id=uuid4(),
        status=status,
        trigger_kind="manual",
        queued_at=now,
        input_json={},
        warnings_json={},
        attempt=1,
        failed_attempts=0,
        max_attempts=3,
        created_at=now,
        updated_at=now,
    )


class TaskLifecycleTests(unittest.TestCase):
    def test_preclaimed_child_lease_does_not_refresh_worker_capacity(self) -> None:
        run = _run("running")
        lease = TaskRunLease(
            run_id=run.id,
            worker_id="supervisor-worker",
            lease_token=uuid4(),
            attempt=run.attempt,
            claimed_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        session = MagicMock()
        session.scalar.return_value = lease
        session.get.return_value = run
        session_factory = MagicMock()
        session_factory.return_value.__enter__.return_value = session

        with patch("tasks.executor.record_worker_heartbeat") as heartbeat:
            _renew_lease(
                session_factory,
                "supervisor-worker",
                run.id,
                lease.lease_token,
                maintain_worker_heartbeat=False,
            )

        heartbeat.assert_not_called()
        session.commit.assert_called_once_with()

    def test_queued_cancellation_is_immediately_terminal(self) -> None:
        run = _run("queued")
        session = MagicMock()
        session.get.return_value = run

        record = request_task_run_cancellation(session, run.id)

        self.assertEqual(record.status, "cancelled")
        self.assertIsNotNone(record.cancellation_requested_at)
        self.assertIsNotNone(record.cancelled_at)
        self.assertIsNotNone(record.finished_at)

    def test_expired_lease_is_requeued_while_attempts_remain(self) -> None:
        run = _run("running")
        lease = TaskRunLease(
            run_id=run.id,
            worker_id="dead-worker",
            lease_token=uuid4(),
            attempt=run.attempt,
            claimed_at=datetime.now(UTC) - timedelta(minutes=1),
            heartbeat_at=datetime.now(UTC) - timedelta(minutes=1),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        session = MagicMock()
        session.scalars.return_value = [run.id]
        session.scalar.side_effect = [lease, run]

        recovered = recover_expired_runs(session)

        self.assertEqual(recovered, 1)
        self.assertEqual(run.status, "queued")
        session.delete.assert_called_once_with(lease)
        self.assertIsNotNone(run.retry_at)

    def test_expired_lease_fails_after_max_attempts(self) -> None:
        run = _run("running")
        run.failed_attempts = run.max_attempts - 1
        lease = TaskRunLease(
            run_id=run.id,
            worker_id="dead-worker",
            lease_token=uuid4(),
            attempt=run.attempt,
            claimed_at=datetime.now(UTC) - timedelta(minutes=1),
            heartbeat_at=datetime.now(UTC) - timedelta(minutes=1),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        session = MagicMock()
        session.scalars.return_value = [run.id]
        session.scalar.side_effect = [lease, run]

        recover_expired_runs(session)

        self.assertEqual(run.status, "failed")
        self.assertIn("3 failed attempts", run.error or "")
