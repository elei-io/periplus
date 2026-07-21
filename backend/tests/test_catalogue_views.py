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

    def test_edit_requires_dematerialization(self) -> None:
        old_uuid = uuid4()
        old_definition = uuid4()
        reference = SimpleNamespace(
            id=uuid4(),
            ducklake_view_uuid=old_uuid,
            schema_name="views",
            view_name="links",
            slug="links",
            description=None,
        )
        materialization = SimpleNamespace(
            source_state="current",
            live_enabled=True,
            backfill_enabled=True,
            definition_revision_id=old_definition,
            bound_ducklake_view_uuid=old_uuid,
            source_sql="SELECT 1 AS value",
            name="links",
        )
        wrapper = DuckLakeView(
            view_uuid=old_uuid,
            schema_name="views",
            view_name="links",
            sql="SELECT * FROM atlas._atlas_materializations.links",
            columns=("value",),
        )
        session = MagicMock()
        session.scalar.side_effect = [
            reference,
            materialization,
        ]
        store = MagicMock()
        store.get.return_value = wrapper
        with self.assertRaises(CatalogueViewConflictError):
            update_reference(
                session,
                store,
                reference,
                expected_uuid=old_uuid,
                sql="SELECT 2 AS value",
                slug="new-links",
                description="Changed",
            )

        self.assertEqual(reference.ducklake_view_uuid, old_uuid)
        self.assertEqual(reference.slug, "links")
        self.assertEqual(materialization.source_sql, "SELECT 1 AS value")
        store.replace.assert_not_called()

if __name__ == "__main__":
    unittest.main()
