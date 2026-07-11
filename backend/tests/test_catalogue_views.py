from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ducklake_client import DiskStorage, DuckDBCatalog

from repository.catalogue import Catalogue, CatalogueConfig
from repository.catalogue.views import (
    CatalogueViewConflictError,
    CatalogueViewError,
    CatalogueViewStore,
)


class CatalogueViewStoreTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
