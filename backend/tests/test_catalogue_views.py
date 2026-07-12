from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from ducklake_client import DiskStorage, DuckDBCatalog

from repository.catalogue import Catalogue, CatalogueConfig
from repository.catalogue.views import (
    CatalogueViewConflictError,
    CatalogueViewError,
    CatalogueViewStore,
    DuckLakeView,
)
from control.catalogue_views.service import (
    detach_reference,
    drop_referenced_view,
    recover_source_change,
    update_reference,
)


class CatalogueViewStoreTests(unittest.TestCase):
    def test_metadata_lookup_uses_catalogue_boundary_not_physical_config(self) -> None:
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = []
        catalogue = SimpleNamespace(
            config=SimpleNamespace(alias="atlas", schema="main"),
            metadata_schema="public",
            connection=connection,
        )

        self.assertEqual(CatalogueViewStore(catalogue).list(), [])
        metadata_sql = connection.execute.call_args_list[0].args[0]
        self.assertIn('"__ducklake_metadata_atlas"."public"', metadata_sql)

    def test_columns_and_types_are_available_after_reconnecting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                CatalogueViewStore(catalogue).create(
                    name="typed_view",
                    sql=(
                        "SELECT CAST('doc' AS VARCHAR) AS document_id, "
                        "current_timestamp AS captured_at"
                    ),
                )

            with Catalogue(config) as catalogue:
                view = CatalogueViewStore(catalogue).list()[0]
                self.assertEqual(view.columns, ("document_id", "captured_at"))
                self.assertEqual(view.column_types[0], "VARCHAR")
                self.assertIn("TIMESTAMP", view.column_types[1])

    def test_create_replace_and_drop_use_ducklake_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                store = CatalogueViewStore(catalogue)
                created = store.create(
                    name="document_counts",
                    sql="SELECT count(*) AS n FROM atlas.main.documents",
                )
                self.assertEqual(created.view_name, "document_counts")
                self.assertEqual(created.columns, ("n",))
                self.assertEqual(len(store.list()), 1)
                self.assertIn("atlas.main.documents", created.sql)

                replaced = store.replace(
                    current_uuid=created.view_uuid,
                    sql="SELECT count(*)::BIGINT AS n FROM documents",
                )
                self.assertNotEqual(replaced.view_uuid, created.view_uuid)
                self.assertIsNone(store.get(created.view_uuid))

                with self.assertRaises(CatalogueViewConflictError):
                    store.replace(current_uuid=created.view_uuid, sql="SELECT 1")

                store.drop(current_uuid=replaced.view_uuid)
                self.assertEqual(store.list(), [])

    def test_rejects_invalid_name_and_mutating_sql(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                store = CatalogueViewStore(catalogue)
                with self.assertRaises(CatalogueViewError):
                    store.create(name="Not Safe", sql="SELECT 1")
                with self.assertRaises(ValueError):
                    store.create(name="danger", sql="DELETE FROM documents")


class CatalogueMaterializationSourceChangeTests(unittest.TestCase):
    def test_attached_materialization_blocks_detach_and_drop(self) -> None:
        reference = SimpleNamespace(id=uuid4(), ducklake_view_uuid=uuid4())
        session = MagicMock()
        session.scalar.return_value = SimpleNamespace(id=uuid4())
        store = MagicMock()

        with self.assertRaisesRegex(
            CatalogueViewConflictError, "Dematerialize this view"
        ):
            detach_reference(session, reference)
        with self.assertRaisesRegex(
            CatalogueViewConflictError, "Dematerialize this view"
        ):
            drop_referenced_view(
                session,
                store,
                reference,
                expected_uuid=reference.ducklake_view_uuid,
            )

        store.drop.assert_not_called()

    def test_edit_commits_fence_before_replacing_view(self) -> None:
        old_uuid = uuid4()
        new_uuid = uuid4()
        old_definition = uuid4()
        reference = SimpleNamespace(
            id=uuid4(),
            ducklake_view_uuid=old_uuid,
            schema_name="views",
            view_name="links",
            display_name="Links",
            description=None,
        )
        materialization = SimpleNamespace(
            source_state="current",
            live_enabled=True,
            backfill_enabled=True,
            definition_revision_id=old_definition,
            bound_ducklake_view_uuid=old_uuid,
        )
        replacement = DuckLakeView(
            view_uuid=new_uuid,
            schema_name="views",
            view_name="links",
            sql="SELECT 2 AS value",
            columns=("value",),
        )
        session = MagicMock()
        session.scalar.side_effect = [
            reference,
            materialization,
            reference,
            materialization,
        ]
        store = MagicMock()

        def replace(**_kwargs):
            session.commit.assert_called_once_with()
            self.assertEqual(materialization.source_state, "source_changing")
            self.assertFalse(materialization.live_enabled)
            self.assertFalse(materialization.backfill_enabled)
            self.assertNotEqual(materialization.definition_revision_id, old_definition)
            return replacement

        store.replace.side_effect = replace
        expected = MagicMock()
        with patch(
            "control.catalogue_views.service._record_with_materialization",
            return_value=expected,
        ):
            result = update_reference(
                session,
                store,
                reference,
                expected_uuid=old_uuid,
                sql="SELECT 2 AS value",
                display_name="New links",
                description="Changed",
            )

        self.assertIs(result, expected)
        self.assertEqual(reference.ducklake_view_uuid, new_uuid)
        self.assertEqual(reference.display_name, "New links")
        self.assertEqual(materialization.source_state, "source_changed")
        self.assertEqual(materialization.bound_ducklake_view_uuid, old_uuid)

    def test_failed_replace_leaves_committed_source_change_fenced(self) -> None:
        old_uuid = uuid4()
        reference = SimpleNamespace(
            id=uuid4(),
            ducklake_view_uuid=old_uuid,
            schema_name="views",
            view_name="links",
            display_name="Links",
            description=None,
        )
        materialization = SimpleNamespace(
            source_state="current",
            live_enabled=True,
            backfill_enabled=True,
            definition_revision_id=uuid4(),
        )
        session = MagicMock()
        session.scalar.side_effect = [reference, materialization]
        store = MagicMock()
        store.replace.side_effect = RuntimeError("DuckLake unavailable")

        with self.assertRaisesRegex(RuntimeError, "DuckLake unavailable"):
            update_reference(
                session,
                store,
                reference,
                expected_uuid=old_uuid,
                sql="SELECT 2",
                display_name=None,
                description=None,
            )

        session.commit.assert_called_once_with()
        self.assertEqual(materialization.source_state, "source_changing")
        self.assertFalse(materialization.live_enabled)
        self.assertFalse(materialization.backfill_enabled)

    def test_recovery_finalizes_a_replacement_completed_before_crash(self) -> None:
        old_uuid = uuid4()
        current = DuckLakeView(
            view_uuid=uuid4(),
            schema_name="views",
            view_name="links",
            sql="SELECT 2",
            columns=("2",),
        )
        reference = SimpleNamespace(
            id=uuid4(),
            ducklake_view_uuid=old_uuid,
            schema_name="views",
            view_name="links",
        )
        materialization = SimpleNamespace(source_state="source_changing")
        session = MagicMock()
        session.scalar.side_effect = [reference, materialization]
        store = MagicMock()
        store.list.return_value = [current]
        expected = MagicMock()
        with patch(
            "control.catalogue_views.service._record_with_materialization",
            return_value=expected,
        ):
            result = recover_source_change(session, store, reference)

        self.assertIs(result, expected)
        self.assertEqual(reference.ducklake_view_uuid, current.view_uuid)
        self.assertEqual(materialization.source_state, "source_changed")


if __name__ == "__main__":
    unittest.main()
