from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from materialization.executor import _commit_scope_fenced, _process_scope
from materialization.queue import MaterializationScopeJob
from materialization.queue import record_materialization_processing_failure
from nats.js.errors import KeyNotFoundError, KeyWrongLastSequenceError
from observability import materialization_metrics
from runtime.resource_governor import ResourceCapacityUnavailable


@asynccontextmanager
async def granted(*_args, **_kwargs):
    yield MagicMock()


def scope_job() -> MaterializationScopeJob:
    return MaterializationScopeJob(
        materialization_id=uuid4(),
        definition_revision_id=uuid4(),
        target_table="page_links",
        scope_kind="crawl",
        scope_column="crawl_id",
        scope_id=str(uuid4()),
        document_id="sha256:document",
        operation_id="a" * 64,
        source="live",
        enqueued_at=datetime.now(UTC),
    )


def scope_message(job: MaterializationScopeJob, *, deliveries: int = 1):
    return SimpleNamespace(
        data=job.model_dump_json().encode(),
        ack=AsyncMock(),
        nak=AsyncMock(),
        term=AsyncMock(),
        in_progress=AsyncMock(),
        metadata=SimpleNamespace(num_delivered=deliveries),
    )


class MaterializationExecutorTests(unittest.IsolatedAsyncioTestCase):
    def test_subphase_timer_records_success_and_failure(self) -> None:
        with (
            patch(
                "observability.materialization_metrics.time.perf_counter",
                side_effect=[1.0, 2.5, 4.0, 7.0],
            ),
            patch(
                "observability.materialization_metrics.operation"
            ) as record_operation,
        ):
            with materialization_metrics.operation_timer("compute.test"):
                pass
            with self.assertRaisesRegex(RuntimeError, "failed"):
                with materialization_metrics.operation_timer("commit.test"):
                    raise RuntimeError("failed")

        self.assertEqual(
            record_operation.call_args_list,
            [
                unittest.mock.call(
                    phase="compute.test",
                    outcome="succeeded",
                    duration_seconds=1.5,
                ),
                unittest.mock.call(
                    phase="commit.test",
                    outcome="failed",
                    duration_seconds=3.0,
                ),
            ],
        )

    def test_local_result_survives_commit_retries_then_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scope.arrow"
            path.write_bytes(b"arrow")
            staged = SimpleNamespace(
                arrow_path=path,
                scope=SimpleNamespace(operation_id="scope"),
            )

            @contextmanager
            def lock(*_args, **_kwargs):
                yield

            attempts = 0

            def commit(_catalogue, _staged):
                nonlocal attempts
                attempts += 1
                self.assertTrue(path.exists())
                if attempts == 1:
                    raise RuntimeError("retry")
                return "committed"

            def retry(function, **_kwargs):
                try:
                    function()
                except RuntimeError:
                    self.assertTrue(path.exists())
                return function()

            with (
                patch("materialization.executor.operation_lock", new=lock),
                patch("materialization.executor.commit_scope", new=commit),
                patch("materialization.executor.run_with_catalogue_retry", new=retry),
            ):
                self.assertEqual(
                    _commit_scope_fenced(MagicMock(), staged), "committed"
                )

            self.assertEqual(attempts, 2)
            self.assertFalse(path.exists())

    async def test_processing_failure_counter_is_independent_of_deliveries(self) -> None:
        class Bucket:
            value = None
            revision = 0

            async def get(self, _key):
                if self.value is None:
                    raise KeyNotFoundError
                return SimpleNamespace(value=self.value, revision=self.revision)

            async def create(self, _key, value):
                if self.value is not None:
                    raise KeyWrongLastSequenceError
                self.revision += 1
                self.value = value

            async def update(self, _key, value, *, last):
                if last != self.revision:
                    raise KeyWrongLastSequenceError
                self.revision += 1
                self.value = value

        bucket = Bucket()
        self.assertEqual(
            await record_materialization_processing_failure(bucket, "scope"), 1
        )
        self.assertEqual(
            await record_materialization_processing_failure(bucket, "scope"), 2
        )

    async def test_successful_scope_computes_and_commits_before_ack(self) -> None:
        job = scope_job()
        message = scope_message(job)
        attempts = SimpleNamespace(delete=AsyncMock())
        staged = SimpleNamespace(arrow_path=MagicMock())
        catalogue_calls = AsyncMock(side_effect=[False, staged, "committed"])

        with (
            patch("materialization.executor.resource_permits", new=granted),
            patch("materialization.executor.operation_leases", new=granted),
            patch(
                "materialization.executor.run_catalogue_operation",
                new=catalogue_calls,
            ),
        ):
            await _process_scope(
                MagicMock(), MagicMock(), message, MagicMock(), MagicMock(), attempts
            )

        self.assertEqual(catalogue_calls.await_count, 3)
        staged.arrow_path.unlink.assert_called_once_with(missing_ok=True)
        message.ack.assert_awaited_once()
        message.nak.assert_not_awaited()
        message.term.assert_not_awaited()

    async def test_authoritative_coverage_skips_recomputation(self) -> None:
        job = scope_job()
        message = scope_message(job)
        attempts = SimpleNamespace(delete=AsyncMock())
        catalogue_calls = AsyncMock(return_value=True)

        with (
            patch("materialization.executor.resource_permits", new=granted),
            patch("materialization.executor.operation_leases", new=granted),
            patch(
                "materialization.executor.run_catalogue_operation",
                new=catalogue_calls,
            ),
        ):
            await _process_scope(
                MagicMock(), MagicMock(), message, MagicMock(), MagicMock(), attempts
            )

        catalogue_calls.assert_awaited_once()
        message.ack.assert_awaited_once()

    async def test_resource_contention_keeps_scope_in_jetstream(self) -> None:
        job = scope_job()
        message = scope_message(job)

        @asynccontextmanager
        async def unavailable(*_args, **_kwargs):
            raise ResourceCapacityUnavailable("busy")
            yield

        with (
            patch("materialization.executor.resource_permits", new=unavailable),
            patch(
                "materialization.executor.record_materialization_processing_failure",
                new=AsyncMock(),
            ) as record_failure,
        ):
            await _process_scope(
                MagicMock(), MagicMock(), message, MagicMock(), MagicMock(), MagicMock()
            )

        message.ack.assert_not_awaited()
        message.term.assert_not_awaited()
        message.nak.assert_awaited_once_with(delay=1)
        record_failure.assert_not_awaited()

    async def test_ack_failure_does_not_consume_processing_budget(self) -> None:
        job = scope_job()
        message = scope_message(job, deliveries=50)
        message.ack.side_effect = OSError("NATS unavailable")

        with (
            patch("materialization.executor.resource_permits", new=granted),
            patch("materialization.executor.operation_leases", new=granted),
            patch(
                "materialization.executor.run_catalogue_operation",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "materialization.executor.record_materialization_processing_failure",
                new=AsyncMock(),
            ) as record_failure,
        ):
            await _process_scope(
                MagicMock(),
                MagicMock(),
                message,
                MagicMock(),
                MagicMock(),
                MagicMock(),
            )

        record_failure.assert_not_awaited()
        message.nak.assert_awaited_once_with(delay=1)

    async def test_terminal_failure_records_coverage_before_dead_letter(self) -> None:
        job = scope_job()
        message = scope_message(job, deliveries=50)
        jetstream = SimpleNamespace(publish=AsyncMock())
        catalogue_calls = AsyncMock(
            side_effect=[False, RuntimeError("invalid scoped SQL"), True]
        )

        with (
            patch("materialization.executor.resource_permits", new=granted),
            patch("materialization.executor.operation_leases", new=granted),
            patch(
                "materialization.executor.run_catalogue_operation",
                new=catalogue_calls,
            ),
            patch(
                "materialization.executor.record_materialization_processing_failure",
                new=AsyncMock(return_value=5),
            ),
            patch(
                "materialization.executor.clear_materialization_processing_failures",
                new=AsyncMock(),
            ),
        ):
            await _process_scope(
                jetstream,
                MagicMock(),
                message,
                MagicMock(),
                MagicMock(),
                MagicMock(),
            )

        self.assertEqual(catalogue_calls.await_count, 3)
        jetstream.publish.assert_awaited_once()
        message.term.assert_awaited_once()
        message.ack.assert_not_awaited()
        message.nak.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
