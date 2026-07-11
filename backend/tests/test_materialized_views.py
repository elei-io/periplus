from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ducklake_client import DiskStorage, DuckDBCatalog

from repository.catalogue import Catalogue, CatalogueConfig
from repository.catalogue.materialized_views import MaterializedViewStore


class MaterializedViewStoreTests(unittest.TestCase):
    def test_create_refresh_and_drop_real_ducklake_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                store = MaterializedViewStore(catalogue)
                created = store.create(name="example", sql="SELECT 1 AS value")
                self.assertEqual(created.row_count, 1)
                self.assertEqual(created.columns[0][0], "value")

                refreshed = store.refresh(
                    name="example",
                    expected_uuid=created.table_uuid,
                    sql="SELECT * FROM range(3) AS values(value)",
                )
                self.assertEqual(refreshed.row_count, 3)
                self.assertNotEqual(refreshed.table_uuid, created.table_uuid)

                store.drop(name="example", expected_uuid=refreshed.table_uuid)
                self.assertEqual(catalogue.lake.table.list(schema_name="materialized"), [])


if __name__ == "__main__":
    unittest.main()
