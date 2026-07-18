from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ducklake_client import DiskStorage, DuckDBCatalog

from repository.catalogue import Catalogue, CatalogueConfig
from repository.catalogue.scalar_macros import CatalogueScalarMacroStore
from repository.catalogue.table_macros import CatalogueTableMacroStore


class CatalogueScalarMacroStoreTests(unittest.TestCase):
    def test_scalar_and_table_macros_can_share_a_qualified_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                scalars = CatalogueScalarMacroStore(catalogue)
                tables = CatalogueTableMacroStore(catalogue)
                scalars.create(name="shared", parameters=["value"], sql="value + 1")
                tables.create(
                    name="shared",
                    parameters=["value"],
                    sql="SELECT value AS result",
                )

                self.assertEqual(
                    catalogue.connection.execute(
                        "SELECT macros.shared(2)"
                    ).fetchone(),
                    (3,),
                )
                self.assertEqual(
                    catalogue.connection.execute(
                        "SELECT * FROM macros.shared(2)"
                    ).fetchone(),
                    (2,),
                )

                tables.drop(name="shared")
                self.assertIsNotNone(scalars.get("shared"))
                self.assertIsNone(tables.get("shared"))


if __name__ == "__main__":
    unittest.main()
