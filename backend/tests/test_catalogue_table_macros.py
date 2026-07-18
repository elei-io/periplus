from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

from ducklake_client import DiskStorage, DuckDBCatalog
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tests.catalogue_helpers import seed_system_macros
from control.catalogue_queries.models import CatalogueQuery, CatalogueQueryRevision
from control.catalogue_table_macros.models import CatalogueTableMacroDefinition
from control.catalogue_table_macros.service import (
    create_definition,
    drop_definition,
    update_definition,
)
from db import Base
from repository.catalogue import Catalogue, CatalogueConfig
from repository.catalogue.table_macros import (
    CatalogueTableMacroConflictError,
    CatalogueTableMacroError,
    CatalogueTableMacroStore,
    DuckLakeTableMacro,
)



class CatalogueTableMacroStoreTests(unittest.TestCase):
    def test_create_replace_list_and_drop_persist_across_connections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                store = CatalogueTableMacroStore(catalogue)
                created = store.create(
                    name="numbers_from",
                    parameters=["p_minimum"],
                    sql=(
                        "SELECT value FROM range(5) AS values(value) "
                        "WHERE value >= p_minimum"
                    ),
                )
                self.assertEqual(created.qualified_name, "macros.numbers_from")
                self.assertEqual(created.parameters, ("p_minimum",))

            with Catalogue(config) as catalogue:
                store = CatalogueTableMacroStore(catalogue)
                self.assertEqual(
                    catalogue.connection.execute(
                        "SELECT * FROM atlas.macros.numbers_from(3) ORDER BY value"
                    ).fetchall(),
                    [(3,), (4,)],
                )
                replaced = store.replace(
                    name="numbers_from",
                    parameters=["p_minimum"],
                    sql=(
                        "SELECT value FROM range(7) AS values(value) "
                        "WHERE value >= p_minimum"
                    ),
                )
                self.assertEqual(replaced.parameters, ("p_minimum",))
                store.drop(name="numbers_from")
                self.assertEqual(store.list(), [])

    def test_preserves_canonical_dom_helpers_in_persisted_definition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                seed_system_macros(catalogue)
                store = CatalogueTableMacroStore(catalogue)
                store.create(
                    name="prices",
                    parameters=["p_document_id"],
                    sql=(
                        "SELECT macros.get_attribute(e.attributes, 'class') AS class "
                        "FROM elements AS e "
                        "WHERE e.document_id = p_document_id "
                        "AND e.tag = 'p'"
                    ),
                )
                self.assertEqual(
                    catalogue.connection.execute(
                        "SELECT * FROM atlas.macros.prices('missing')"
                    ).fetchall(),
                    [],
                )

    def test_optional_parameter_defaults_persist_across_connections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                CatalogueTableMacroStore(catalogue).create(
                    name="numbers_between",
                    parameters=["p_minimum", "p_maximum"],
                    parameter_defaults={"p_maximum": "4"},
                    sql=(
                        "SELECT value FROM range(p_maximum) AS values(value) "
                        "WHERE value >= p_minimum"
                    ),
                )

            with Catalogue(config) as catalogue:
                self.assertEqual(
                    catalogue.connection.execute(
                        "SELECT * FROM atlas.macros.numbers_between(2) ORDER BY value"
                    ).fetchall(),
                    [(2,), (3,)],
                )

    def test_rejects_invalid_names_duplicate_parameters_and_mutating_sql(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                store = CatalogueTableMacroStore(catalogue)
                with self.assertRaises(CatalogueTableMacroError):
                    store.create(name="Not Safe", parameters=[], sql="SELECT 1")
                with self.assertRaises(CatalogueTableMacroError):
                    store.create(
                        name="duplicate",
                        parameters=["value", "value"],
                        sql="SELECT 1",
                    )
                with self.assertRaises(CatalogueTableMacroError):
                    store.create(
                        name="misordered",
                        parameters=["optional", "required"],
                        parameter_defaults={"optional": "1"},
                        sql="SELECT 1",
                    )
                with self.assertRaises(ValueError):
                    store.create(name="danger", parameters=[], sql="DELETE FROM documents")
                store.create(name="exists", parameters=[], sql="SELECT 1")
                with self.assertRaises(CatalogueTableMacroConflictError):
                    store.create(name="exists", parameters=[], sql="SELECT 2")


class CatalogueTableMacroDefinitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            self.engine,
            tables=[
                CatalogueQuery.__table__,
                CatalogueQueryRevision.__table__,
                CatalogueTableMacroDefinition.__table__,
            ],
        )
        self.session = Session(self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_create_update_and_drop_use_revision_fencing(self) -> None:
        store = MagicMock()
        store.create.return_value = DuckLakeTableMacro(
            schema_name="macros",
            macro_name="selector_stats",
            parameters=("hostname",),
        )
        created = create_definition(
            self.session,
            store,
            slug="selector_stats",
            parameters=["hostname"],
            sql="SELECT hostname AS host",
            description=None,
        )
        definition = self.session.get(CatalogueTableMacroDefinition, created.id)
        self.assertIsNotNone(definition)

        with self.assertRaises(CatalogueTableMacroConflictError):
            update_definition(
                self.session,
                store,
                definition,
                expected_revision_id=uuid4(),
                parameters=["hostname"],
                sql="SELECT hostname AS host",
                slug="selector_stats",
                description=None,
            )

        store.replace.return_value = DuckLakeTableMacro(
            schema_name="macros",
            macro_name="selector_stats",
            parameters=("hostname", "path_pattern"),
        )
        updated = update_definition(
            self.session,
            store,
            definition,
            expected_revision_id=created.definition_revision_id,
            parameters=["hostname", "path_pattern"],
            sql="SELECT hostname AS host, path_pattern AS path",
            slug="selector-profiles",
            description="Updated",
        )
        self.assertNotEqual(
            updated.definition_revision_id, created.definition_revision_id
        )
        self.assertEqual(updated.parameters, ["hostname", "path_pattern"])

        drop_definition(
            self.session,
            store,
            definition,
            expected_revision_id=updated.definition_revision_id,
        )
        self.assertIsNone(
            self.session.get(CatalogueTableMacroDefinition, definition.id)
        )


if __name__ == "__main__":
    unittest.main()
