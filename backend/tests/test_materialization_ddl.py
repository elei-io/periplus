from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from materialization.executor import _apply_ddl_event
from runtime.catalogue_events import CatalogueDDLEvent


def event(*, kind: str, object_id: int, snapshot_id: int = 20):
    return CatalogueDDLEvent(
        event_kind=kind,
        object_kind="table",
        snapshot_id=snapshot_id,
        snapshot_time=None,
        schema_id=1,
        schema_name="main",
        object_id=object_id,
        object_name="source_or_target",
        details=None,
    )


@contextmanager
def session_returning(model):
    session = MagicMock()
    session.scalars.return_value = [model]
    yield session


class MaterializationDDLTests(unittest.TestCase):
    def model(self):
        return SimpleNamespace(
            source_table_id=7,
            target_table_id=9,
            control_snapshot=10,
            bootstrap_snapshot=15,
            desired_state="live",
            observed_state="live",
            last_error=None,
        )

    def apply(self, model, change) -> None:
        with patch(
            "materialization.executor.session_scope",
            return_value=session_returning(model),
        ):
            _apply_ddl_event(change)

    def test_source_rename_blocks_the_incarnation(self) -> None:
        model = self.model()

        self.apply(model, event(kind="renamed", object_id=7))

        self.assertEqual(model.observed_state, "blocked_schema")
        self.assertIn("Driving table", model.last_error)
        self.assertIn("renamed", model.last_error)
        self.assertEqual(model.control_snapshot, 10)

    def test_target_alter_blocks_the_incarnation(self) -> None:
        model = self.model()

        self.apply(model, event(kind="altered", object_id=9))

        self.assertEqual(model.observed_state, "blocked_schema")
        self.assertIn("Materialization target", model.last_error)
        self.assertEqual(model.control_snapshot, 10)

    def test_source_or_target_drop_deletes_the_incarnation(self) -> None:
        for object_id in (7, 9):
            with self.subTest(object_id=object_id):
                model = self.model()

                self.apply(model, event(kind="dropped", object_id=object_id))

                self.assertEqual(model.desired_state, "deleting")
                self.assertEqual(model.observed_state, "deleting")
                self.assertEqual(model.control_snapshot, 10)

    def test_replayed_or_older_ddl_cannot_regress_state(self) -> None:
        model = self.model()
        model.control_snapshot = 25

        self.apply(
            model,
            event(kind="dropped", object_id=7, snapshot_id=20),
        )

        self.assertEqual(model.desired_state, "live")
        self.assertEqual(model.observed_state, "live")
        self.assertEqual(model.control_snapshot, 25)

    def test_more_than_one_event_from_the_same_snapshot_is_applied(self) -> None:
        model = self.model()

        self.apply(model, event(kind="created", object_id=9))
        self.apply(model, event(kind="altered", object_id=7))

        self.assertEqual(model.observed_state, "blocked_schema")
        self.assertEqual(model.control_snapshot, 10)

    def test_bootstrap_target_ddl_is_not_an_external_schema_change(self) -> None:
        model = self.model()

        self.apply(
            model,
            event(kind="altered", object_id=9, snapshot_id=15),
        )

        self.assertEqual(model.observed_state, "live")
        self.assertIsNone(model.last_error)


if __name__ == "__main__":
    unittest.main()
