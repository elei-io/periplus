from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from materialization.executor import _process_scope
from materialization.queue import MaterializationScopeJob
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
    async def test_successful_scope_computes_and_commits_before_ack(self) -> None:
        job = scope_job()
        message = scope_message(job)
        staged = object()
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
                MagicMock(), MagicMock(), message, MagicMock(), MagicMock()
            )

        self.assertEqual(catalogue_calls.await_count, 3)
        message.ack.assert_awaited_once()
        message.nak.assert_not_awaited()
        message.term.assert_not_awaited()

    async def test_authoritative_coverage_skips_recomputation(self) -> None:
        job = scope_job()
        message = scope_message(job)
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
                MagicMock(), MagicMock(), message, MagicMock(), MagicMock()
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

        with patch("materialization.executor.resource_permits", new=unavailable):
            await _process_scope(
                MagicMock(), MagicMock(), message, MagicMock(), MagicMock()
            )

        message.ack.assert_not_awaited()
        message.term.assert_not_awaited()
        message.nak.assert_awaited_once_with(delay=1)

    async def test_terminal_failure_records_coverage_before_dead_letter(self) -> None:
        job = scope_job()
        message = scope_message(job, deliveries=5)
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
        ):
            await _process_scope(
                jetstream, MagicMock(), message, MagicMock(), MagicMock()
            )

        self.assertEqual(catalogue_calls.await_count, 3)
        jetstream.publish.assert_awaited_once()
        message.term.assert_awaited_once()
        message.ack.assert_not_awaited()
        message.nak.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
