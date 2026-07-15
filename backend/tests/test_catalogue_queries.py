from __future__ import annotations

import unittest
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from control.catalogue_queries.models import CatalogueQuery, CatalogueQueryRevision
from control.catalogue_queries.service import (
    CatalogueQueryConflictError,
    create_query,
    restore_revision,
    update_query,
)
from db import Base


class CatalogueQueryRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            self.engine,
            tables=[CatalogueQuery.__table__, CatalogueQueryRevision.__table__],
        )
        self.session = Session(self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_edit_and_restore_append_linear_revisions(self) -> None:
        created = create_query(
            self.session,
            name="Example",
            description=None,
            sql="SELECT 1 AS value",
            change_note="Initial",
        )
        query = self.session.get(CatalogueQuery, created.id)
        updated = update_query(
            self.session,
            query,
            expected_revision_id=created.current_revision_id,
            sql="SELECT 2 AS value",
            name="Example",
            description=None,
            change_note="Second",
        )
        restored = restore_revision(
            self.session,
            query,
            query.revisions[0],
            expected_revision_id=updated.current_revision_id,
            change_note=None,
        )
        self.assertEqual(restored.current_revision, 3)
        self.assertEqual([item.revision for item in restored.revisions], [3, 2, 1])
        self.assertEqual(restored.sql, "SELECT 1 AS value")

    def test_identical_sql_does_not_create_revision_and_stale_save_conflicts(self) -> None:
        created = create_query(
            self.session,
            name="Example",
            description=None,
            sql="SELECT 1",
            change_note=None,
        )
        query = self.session.get(CatalogueQuery, created.id)
        unchanged = update_query(
            self.session,
            query,
            expected_revision_id=created.current_revision_id,
            sql="SELECT 1",
            name="Renamed",
            description=None,
            change_note=None,
        )
        self.assertEqual(unchanged.current_revision, 1)
        with self.assertRaises(CatalogueQueryConflictError):
            update_query(
                self.session,
                query,
                expected_revision_id=uuid4(),
                sql="SELECT 2",
                name=None,
                description=None,
                change_note=None,
            )


if __name__ == "__main__":
    unittest.main()
