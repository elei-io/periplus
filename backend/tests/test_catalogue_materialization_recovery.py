from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from control.catalogue_materializations.service import update_state
from repository.catalogue.materializations import MaterializationConflictError


class CatalogueMaterializationRecoveryTests(unittest.TestCase):
    def _resume(self, model: SimpleNamespace) -> SimpleNamespace:
        session = MagicMock()
        with patch(
            "control.catalogue_materializations.service.record",
            side_effect=lambda _session, current: current,
        ):
            return update_state(
                session,
                model,
                desired_state="live",
                refresh_delay_seconds=None,
            )

    def test_failed_creation_restarts_without_discarding_incarnation(self) -> None:
        model = SimpleNamespace(
            desired_state="live",
            observed_state="failed",
            ducklake_table_uuid=None,
            bootstrap_partition_count=None,
            last_error="remote commit failed",
        )

        resumed = self._resume(model)

        self.assertEqual(resumed.observed_state, "creating")
        self.assertIsNone(resumed.last_error)

    def test_failed_backfill_resumes_from_persisted_partition_cursor(self) -> None:
        table_uuid = uuid4()
        model = SimpleNamespace(
            desired_state="live",
            observed_state="failed",
            ducklake_table_uuid=table_uuid,
            bootstrap_partition_count=100,
            bootstrap_partition_cursor=37,
            last_error="remote commit failed",
        )

        resumed = self._resume(model)

        self.assertEqual(resumed.observed_state, "backfilling")
        self.assertEqual(resumed.bootstrap_partition_cursor, 37)
        self.assertEqual(resumed.ducklake_table_uuid, table_uuid)

    def test_failed_live_refresh_resumes_with_existing_target(self) -> None:
        table_uuid = uuid4()
        model = SimpleNamespace(
            desired_state="live",
            observed_state="failed",
            ducklake_table_uuid=table_uuid,
            bootstrap_partition_count=None,
            processed_snapshot=91,
            last_error="remote commit failed",
        )

        resumed = self._resume(model)

        self.assertEqual(resumed.observed_state, "live")
        self.assertEqual(resumed.processed_snapshot, 91)
        self.assertEqual(resumed.ducklake_table_uuid, table_uuid)

    def test_schema_block_remains_non_resumable(self) -> None:
        model = SimpleNamespace(
            desired_state="live",
            observed_state="blocked_schema",
        )

        with self.assertRaises(MaterializationConflictError):
            self._resume(model)


if __name__ == "__main__":
    unittest.main()
