from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import duckdb

from materialization.executor import (
    _apply_ticks,
    _refresh_messages,
    _tracked_operation,
)
from runtime.catalogue_events import CatalogueDMLTick
from runtime.catalogue_workers import CatalogueLaneReporter
from runtime.operation_leases import OperationLeaseLost

class MaterializationAcknowledgementTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_operation_count_is_scoped_to_domain_work(self) -> None:
        counter = CatalogueLaneReporter(lane_index=0)

        async def operation() -> str:
            self.assertEqual(counter.active_operation_count, 1)
            return "done"

        result = await _tracked_operation(counter, operation())

        self.assertEqual(result, "done")
        self.assertEqual(counter.active_operation_count, 0)

    async def test_tick_application_commits_without_acking_delivery(self) -> None:
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
            side_effect=TimeoutError()
        )
        definition = SimpleNamespace(
            id=uuid4(),
            refresh_delay_seconds=0,
            processed_snapshot=10,
        )

        def refresh(*_args, **_kwargs):
            events.append("target_commit")

        with patch(
            "materialization.executor._refresh_materialization",
            side_effect=refresh,
        ) as refresh_materialization:
            worked = await _apply_ticks(
                MagicMock(),
                subscription,
                definition,
                [message],
            )

        self.assertTrue(worked)
        self.assertEqual(events, ["target_commit"])
        message.ack.assert_not_awaited()
        refresh_materialization.assert_called_once_with(
            ANY, definition.id, 20, 20
        )

    async def test_unrelated_catalogue_schema_change_does_not_block_refresh(
        self,
    ) -> None:
        events: list[str] = []
        message = MagicMock()
        message.data = CatalogueDMLTick(
            table_id=7,
            table_uuid=uuid4(),
            schema_name="main",
            table_name="crawls",
            snapshot_id=20,
            snapshot_time=None,
            # DuckLake schema versions are catalogue-wide. An unrelated view
            # recreation can advance this value without changing `crawls`.
            schema_version=2,
        ).model_dump_json().encode()
        message.ack = AsyncMock(side_effect=lambda: events.append("ack"))
        subscription = MagicMock()
        subscription.fetch = AsyncMock(
            side_effect=TimeoutError()
        )
        definition = SimpleNamespace(
            id=uuid4(),
            refresh_delay_seconds=0,
            processed_snapshot=10,
        )

        def refresh(*_args, **_kwargs):
            events.append("target_commit")

        with (
            patch(
                "materialization.executor._refresh_materialization",
                side_effect=refresh,
            ),
            patch("materialization.executor._mark_blocked") as mark_blocked,
        ):
            worked = await _apply_ticks(
                MagicMock(),
                subscription,
                definition,
                [message],
            )

        self.assertTrue(worked)
        self.assertEqual(events, ["target_commit"])
        message.ack.assert_not_awaited()
        mark_blocked.assert_not_called()

    async def test_ticks_are_acked_only_after_lease_exit(self) -> None:
        events: list[str] = []
        message = MagicMock()
        message.ack = AsyncMock(side_effect=lambda: events.append("ack"))
        definition = SimpleNamespace(id=uuid4())

        class Lease:
            async def __aenter__(self):
                events.append("lease_enter")

            async def __aexit__(self, *_args):
                events.append("lease_exit")

        async def apply_ticks(*_args):
            events.append("target_commit")
            return True

        with (
            patch(
                "materialization.executor.operation_leases",
                return_value=Lease(),
            ),
            patch(
                "materialization.executor._apply_ticks",
                side_effect=apply_ticks,
            ),
        ):
            worked = await _refresh_messages(
                CatalogueLaneReporter(lane_index=0),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                definition,
                [message],
            )

        self.assertTrue(worked)
        self.assertEqual(
            events,
            ["lease_enter", "target_commit", "lease_exit", "ack"],
        )

    async def test_lost_lease_returns_committed_ticks_for_redelivery(self) -> None:
        events: list[str] = []
        message = MagicMock()
        message.ack = AsyncMock()
        message.nak = AsyncMock(side_effect=lambda **_kwargs: events.append("nak"))
        definition = SimpleNamespace(id=uuid4())

        class LostLease:
            async def __aenter__(self):
                events.append("lease_enter")

            async def __aexit__(self, *_args):
                events.append("lease_lost")
                raise OperationLeaseLost(
                    "catalogue materialization operation lease was lost"
                )

        async def apply_ticks(*_args):
            events.append("target_commit")
            return True

        with (
            patch(
                "materialization.executor.operation_leases",
                return_value=LostLease(),
            ),
            patch(
                "materialization.executor._apply_ticks",
                side_effect=apply_ticks,
            ),
        ):
            worked = await _refresh_messages(
                CatalogueLaneReporter(lane_index=0),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                definition,
                [message],
            )

        self.assertFalse(worked)
        self.assertEqual(
            events,
            ["lease_enter", "target_commit", "lease_lost", "nak"],
        )
        message.ack.assert_not_awaited()
        message.nak.assert_awaited_once_with(delay=1)

    async def test_persistent_compaction_conflict_returns_ticks_for_redelivery(
        self,
    ) -> None:
        message = MagicMock()
        message.nak = AsyncMock()
        definition = SimpleNamespace(id=uuid4())
        lease = AsyncMock()
        lease.__aenter__.return_value = None
        lease.__aexit__.return_value = None
        conflict = duckdb.InvalidInputException(
            "Failed to commit DuckLake transaction. Transaction conflict - "
            "attempting to delete from table with index \"430\" - but another "
            "transaction has compacted it"
        )

        with (
            patch(
                "materialization.executor.operation_leases",
                return_value=lease,
            ),
            patch(
                "materialization.executor._apply_ticks",
                AsyncMock(side_effect=conflict),
            ),
        ):
            worked = await _refresh_messages(
                CatalogueLaneReporter(lane_index=0),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                definition,
                [message],
            )

        self.assertFalse(worked)
        message.nak.assert_awaited_once_with(delay=1)


if __name__ == "__main__":
    unittest.main()
