from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from nats.errors import TimeoutError as NatsTimeoutError

from materialization.executor import (
    _reconcile_ddl,
    _refresh_from_ticks_owned,
    _tracked_operation,
)
from runtime.catalogue_events import CatalogueDMLTick


@asynccontextmanager
async def admitted(*_args, **_kwargs):
    yield


class MaterializationAcknowledgementTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_pull_timeout_is_idle_not_worker_failure(self) -> None:
        subscription = MagicMock()
        subscription.fetch = AsyncMock(side_effect=TimeoutError())

        self.assertFalse(await _reconcile_ddl(subscription))

    async def test_active_operation_count_is_scoped_to_domain_work(self) -> None:
        counter = [0]

        async def operation() -> str:
            self.assertEqual(counter, [1])
            return "done"

        result = await _tracked_operation(counter, operation())

        self.assertEqual(result, "done")
        self.assertEqual(counter, [0])

    async def test_ticks_are_acked_only_after_target_commit(self) -> None:
        events: list[str] = []
        message = MagicMock()
        message.data = CatalogueDMLTick(
            table_id=7,
            table_uuid=uuid4(),
            schema_name="main",
            table_name="documents",
            snapshot_id=20,
            snapshot_time=None,
            schema_version=1,
        ).model_dump_json().encode()
        message.ack = AsyncMock(side_effect=lambda: events.append("ack"))
        subscription = MagicMock()
        subscription.fetch = AsyncMock(
            side_effect=[[message], NatsTimeoutError()]
        )
        definition = SimpleNamespace(
            id=uuid4(),
            refresh_delay_seconds=0,
            processed_snapshot=10,
            source_schema_version=1,
        )

        def refresh(*_args, **_kwargs):
            events.append("target_commit")

        with (
            patch(
                "materialization.executor._materialization_permit",
                new=admitted,
            ),
            patch(
                "materialization.executor._refresh_materialization",
                side_effect=refresh,
            ),
        ):
            counter = [0]
            worked = await _refresh_from_ticks_owned(
                counter,
                MagicMock(),
                MagicMock(),
                subscription,
                definition,
            )

        self.assertTrue(worked)
        self.assertEqual(events, ["target_commit", "ack"])
        self.assertEqual(counter, [0])


if __name__ == "__main__":
    unittest.main()
