from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

from materialization.executor import (
    _apply_ticks,
    _tracked_operation,
)
from runtime.catalogue_events import CatalogueDMLTick

class MaterializationAcknowledgementTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertEqual(events, ["target_commit", "ack"])
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
        self.assertEqual(events, ["target_commit", "ack"])
        mark_blocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
