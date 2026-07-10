from __future__ import annotations

import asyncio
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from tasks.executor import (
    PrimitiveExecution,
    TaskRunLeaseLost,
    _lock_owned_run,
    _renew_lease,
    claim_next_run,
    recover_expired_runs,
    release_task_run,
    release_worker_runs,
    run_worker_once,
)
from tasks.context import TaskExecutionContext, commit_task_checkpoint, task_execution_scope
from tasks.models import Task, TaskRun, TaskRunLease
from tasks.notifications import RunQueueListener
from tasks.service import enqueue_ad_hoc_task_run, request_task_run_cancellation, task_operations
from crawl_policies.models import CrawlPermit
from crawl_policies.permits import _try_acquire, capacity_lease


class PostgresTaskLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        database_url = os.getenv("ATLAS_TEST_DATABASE_URL")
        if not database_url:
            raise unittest.SkipTest("ATLAS_TEST_DATABASE_URL is not configured.")
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        cls.database_url = database_url
        cls.engine = create_engine(database_url, pool_pre_ping=True)
        cls.Session = sessionmaker(cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "engine"):
            cls.engine.dispose()

    def tearDown(self) -> None:
        if not hasattr(self, "Session"):
            return
        with self.Session.begin() as session:
            session.execute(delete(Task).where(Task.identity_key.like("test:lifecycle:%")))

    def test_enqueue_cancel_operations_and_lease_fencing(self) -> None:
        query = f"lifecycle-{uuid4()}"
        with self.Session.begin() as session:
            submission = enqueue_ad_hoc_task_run(
                session,
                primitive="search",
                input_value={"query": query, "max_pages": 1, "provider": "duckduckgo"},
            )
            task = session.get(Task, submission.task_id)
            assert task is not None
            run = session.get(TaskRun, submission.run_id)
            assert run is not None
            self.assertEqual(run.task_revision, task.revision)
            task.identity_key = f"test:lifecycle:{uuid4()}"

        with self.Session.begin() as session:
            cancelled = request_task_run_cancellation(session, submission.run_id)
            self.assertEqual(cancelled.status, "cancelled")
            summary = task_operations(session)
            self.assertGreaterEqual(summary.queued, 0)
            self.assertGreaterEqual(summary.running, 0)

        with self.Session.begin() as session:
            run = session.get(TaskRun, submission.run_id)
            assert run is not None
            run.status = "running"
            run.finished_at = None
            run.cancelled_at = None
            run.cancellation_requested_at = None
            lease = TaskRunLease(
                run_id=run.id,
                worker_id="new-worker",
                lease_token=uuid4(),
                attempt=run.attempt,
                claimed_at=datetime.now(UTC),
                heartbeat_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            session.add(lease)
            lease_token = lease.lease_token

        with self.Session() as session:
            with self.assertRaises(TaskRunLeaseLost):
                _lock_owned_run(session, submission.run_id, uuid4())
            session.rollback()
            owned = _lock_owned_run(session, submission.run_id, lease_token)
            self.assertEqual(owned.id, submission.run_id)
            session.rollback()

    def test_queued_trigger_wakes_postgres_listener(self) -> None:
        async def exercise() -> None:
            listener = RunQueueListener(self.database_url.replace("postgresql+psycopg://", "postgresql://", 1))
            await listener._connect()
            try:
                with self.Session.begin() as session:
                    submission = enqueue_ad_hoc_task_run(
                        session,
                        primitive="search",
                        input_value={
                            "query": f"notify-{uuid4()}",
                            "max_pages": 1,
                            "provider": "duckduckgo",
                        },
                    )
                    task = session.get(Task, submission.task_id)
                    assert task is not None
                    task.identity_key = f"test:lifecycle:{uuid4()}"
                self.assertTrue(await listener.wait(1))
            finally:
                await listener.close()

        asyncio.run(exercise())

    def test_lease_heartbeat_is_independent_of_locked_run_row(self) -> None:
        with self.Session.begin() as session:
            submission = enqueue_ad_hoc_task_run(
                session,
                primitive="search",
                input_value={
                    "query": f"heartbeat-lock-{uuid4()}",
                    "max_pages": 1,
                    "provider": "duckduckgo",
                },
            )
            task = session.get(Task, submission.task_id)
            assert task is not None
            task.identity_key = f"test:lifecycle:{uuid4()}"

        with self.Session.begin() as session:
            run = claim_next_run(session, worker_id="heartbeat-worker")
            assert run is not None and run.id == submission.run_id and run.lease is not None
            lease_token = run.lease.lease_token

        with self.Session() as locking_session:
            locked_run = locking_session.get(TaskRun, submission.run_id)
            assert locked_run is not None
            locked_run.error = "uncommitted action checkpoint"
            locking_session.flush()

            with ThreadPoolExecutor(max_workers=1) as pool:
                heartbeat = pool.submit(
                    _renew_lease,
                    self.Session,
                    "heartbeat-worker",
                    submission.run_id,
                    lease_token,
                )
                heartbeat.result(timeout=3)
            locking_session.rollback()

    def test_expired_token_cannot_checkpoint_or_resurrect(self) -> None:
        with self.Session.begin() as session:
            submission = enqueue_ad_hoc_task_run(
                session,
                primitive="search",
                input_value={
                    "query": f"expired-token-{uuid4()}",
                    "max_pages": 1,
                    "provider": "duckduckgo",
                },
            )
            task = session.get(Task, submission.task_id)
            assert task is not None
            task.identity_key = f"test:lifecycle:{uuid4()}"

        with self.Session.begin() as session:
            run = claim_next_run(session, worker_id="expired-worker")
            assert run is not None and run.lease is not None
            lease_token = run.lease.lease_token
            attempt = run.attempt
            run.lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)

        with self.assertRaises(TaskRunLeaseLost):
            _renew_lease(self.Session, "expired-worker", submission.run_id, lease_token)

        with task_execution_scope(
            TaskExecutionContext(
                run_id=submission.run_id,
                attempt=attempt,
                lease_token=lease_token,
            )
        ):
            with self.Session() as session:
                run = session.get(TaskRun, submission.run_id)
                assert run is not None
                run.error = "must roll back"
                with self.assertRaisesRegex(RuntimeError, "lost ownership"):
                    commit_task_checkpoint(session)

        with self.Session.begin() as session:
            self.assertEqual(recover_expired_runs(session), 1)
        with self.Session() as session:
            run = session.get(TaskRun, submission.run_id)
            assert run is not None
            self.assertEqual(run.status, "queued")
            self.assertIsNone(run.error)
            self.assertIsNone(session.get(TaskRunLease, submission.run_id))

    def test_shutdown_releases_owned_run_immediately(self) -> None:
        with self.Session.begin() as session:
            submission = enqueue_ad_hoc_task_run(
                session,
                primitive="search",
                input_value={
                    "query": f"shutdown-release-{uuid4()}",
                    "max_pages": 1,
                    "provider": "duckduckgo",
                },
            )
            task = session.get(Task, submission.task_id)
            assert task is not None
            task.identity_key = f"test:lifecycle:{uuid4()}"

        with self.Session.begin() as session:
            claimed = claim_next_run(session, worker_id="stopping-worker")
            assert claimed is not None and claimed.id == submission.run_id

        with self.Session.begin() as session:
            self.assertEqual(release_worker_runs(session, "stopping-worker"), 1)

        with self.Session() as session:
            run = session.get(TaskRun, submission.run_id)
            assert run is not None
            self.assertEqual(run.status, "queued")
            self.assertIsNone(run.started_at)
            self.assertEqual(run.failed_attempts, 0)
            self.assertEqual(run.attempt, 1)
            self.assertIsNone(session.get(TaskRunLease, submission.run_id))

    def test_stale_release_cannot_touch_newer_attempt(self) -> None:
        with self.Session.begin() as session:
            submission = enqueue_ad_hoc_task_run(
                session,
                primitive="search",
                input_value={
                    "query": f"stale-release-{uuid4()}",
                    "max_pages": 1,
                    "provider": "duckduckgo",
                },
            )
            task = session.get(Task, submission.task_id)
            assert task is not None
            task.identity_key = f"test:lifecycle:{uuid4()}"

        with self.Session.begin() as session:
            first = claim_next_run(session, worker_id="old-worker")
            assert first is not None and first.lease is not None
            old_token = first.lease.lease_token
            first.lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with self.Session.begin() as session:
            self.assertEqual(recover_expired_runs(session), 1)
        with self.Session.begin() as session:
            second = claim_next_run(session, worker_id="new-worker")
            assert second is not None and second.lease is not None
            new_token = second.lease.lease_token

        with self.Session.begin() as session:
            self.assertFalse(
                release_task_run(session, submission.run_id, lease_token=old_token)
            )
        with self.Session() as session:
            run = session.get(TaskRun, submission.run_id)
            lease = session.get(TaskRunLease, submission.run_id)
            assert run is not None and lease is not None
            self.assertEqual(run.status, "running")
            self.assertEqual(lease.lease_token, new_token)

    def test_global_permit_capacity_is_shared(self) -> None:
        query = f"permit-{uuid4()}"
        with self.Session.begin() as session:
            submission = enqueue_ad_hoc_task_run(
                session,
                primitive="search",
                input_value={"query": query, "max_pages": 1, "provider": "duckduckgo"},
            )
            task = session.get(Task, submission.task_id)
            assert task is not None
            task.identity_key = f"test:lifecycle:{uuid4()}"
            run = session.get(TaskRun, submission.run_id)
            assert run is not None
            run.status = "running"
            lease = TaskRunLease(
                run_id=run.id,
                worker_id="permit-worker",
                lease_token=uuid4(),
                attempt=run.attempt,
                claimed_at=datetime.now(UTC),
                heartbeat_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            session.add(lease)

        permit_key = f"test:browser:{uuid4()}"
        with self.Session.begin() as session:
            first = _try_acquire(
                session,
                permit_key=permit_key,
                capacity=1,
                task_run_id=submission.run_id,
                worker_id="permit-worker",
                policy_id=None,
                lease_seconds=60,
            )
            second = _try_acquire(
                session,
                permit_key=permit_key,
                capacity=1,
                task_run_id=submission.run_id,
                worker_id="permit-worker",
                policy_id=None,
                lease_seconds=60,
            )
            self.assertIsNotNone(first)
            self.assertIsNone(second)
            session.execute(delete(CrawlPermit).where(CrawlPermit.permit_key == permit_key))

    def test_two_worker_slots_execute_runs_concurrently(self) -> None:
        submissions = []
        with self.Session.begin() as session:
            for _ in range(2):
                submission = enqueue_ad_hoc_task_run(
                    session,
                    primitive="search",
                    input_value={
                        "query": f"concurrent-{uuid4()}",
                        "max_pages": 1,
                        "provider": "duckduckgo",
                    },
                )
                task = session.get(Task, submission.task_id)
                assert task is not None
                task.identity_key = f"test:lifecycle:{uuid4()}"
                run = session.get(TaskRun, submission.run_id)
                assert run is not None
                run.queued_at = datetime(2000, 1, 1, tzinfo=UTC)
                submissions.append(submission)

        active = 0
        peak = 0

        async def fake_execute(*_args, **_kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1
            return PrimitiveExecution(output_json={"results": []}, response_json=[], warnings=[])

        async def exercise() -> None:
            with patch("tasks.executor._execute_primitive", side_effect=fake_execute):
                claimed = await asyncio.gather(
                    run_worker_once(self.Session, worker_id="concurrent-worker"),
                    run_worker_once(self.Session, worker_id="concurrent-worker"),
                )
            self.assertEqual(claimed, [True, True])

        asyncio.run(exercise())
        self.assertEqual(peak, 2)
        with self.Session() as session:
            statuses = [session.get(TaskRun, submission.run_id).status for submission in submissions]
        self.assertEqual(statuses, ["succeeded", "succeeded"])

    def test_capacity_lease_releases_after_failure(self) -> None:
        with self.Session.begin() as session:
            submission = enqueue_ad_hoc_task_run(
                session,
                primitive="search",
                input_value={
                    "query": f"permit-release-{uuid4()}",
                    "max_pages": 1,
                    "provider": "duckduckgo",
                },
            )
            task = session.get(Task, submission.task_id)
            assert task is not None
            task.identity_key = f"test:lifecycle:{uuid4()}"
            run = session.get(TaskRun, submission.run_id)
            assert run is not None
            run.status = "running"
            release_lease = TaskRunLease(
                run_id=run.id,
                worker_id="permit-release-worker",
                lease_token=uuid4(),
                attempt=run.attempt,
                claimed_at=datetime.now(UTC),
                heartbeat_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            session.add(release_lease)
            permit_lease_token = release_lease.lease_token
            permit_attempt = run.attempt

        async def exercise() -> None:
            with task_execution_scope(
                TaskExecutionContext(
                    run_id=submission.run_id,
                    attempt=permit_attempt,
                    lease_token=permit_lease_token,
                )
            ):
                with self.Session() as session:
                    with self.assertRaisesRegex(RuntimeError, "page failed"):
                        async with capacity_lease(
                            session,
                            task_run_id=submission.run_id,
                            url="https://example.com/page",
                            policy=None,
                            progress_reporter=None,
                        ):
                            raise RuntimeError("page failed")

        asyncio.run(exercise())
        with self.Session() as session:
            permits = list(
                session.scalars(select(CrawlPermit).where(CrawlPermit.task_run_id == submission.run_id))
            )
        self.assertEqual(permits, [])
