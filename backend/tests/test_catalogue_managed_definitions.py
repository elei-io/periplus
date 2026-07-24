from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from control import catalogue_fixtures
from control.catalogue_fixtures import RelationFixture, seed_catalogue_fixtures
from repository.catalogue.scalar_macros import (
    CatalogueScalarMacroStore,
    DuckLakeScalarMacro,
)
from repository.catalogue.table_macros import (
    CatalogueTableMacroStore,
    DuckLakeTableMacro,
)
from repository.catalogue.views import CatalogueViewStore, DuckLakeView


def _catalogue() -> MagicMock:
    catalogue = MagicMock()
    catalogue.config = SimpleNamespace(alias="atlas", schema="main")
    return catalogue


class ManagedDefinitionStoreTests(unittest.TestCase):
    def test_view_list_uses_remote_metadata_and_describes_columns(self) -> None:
        catalogue = _catalogue()
        catalogue.trusted_remote_rows.side_effect = [
            [
                (
                    "views",
                    "document_counts",
                    "CREATE VIEW document_counts AS SELECT 1::BIGINT AS n",
                )
            ],
            [("n", "BIGINT")],
        ]

        views = CatalogueViewStore(catalogue).list()

        self.assertEqual(len(views), 1)
        self.assertEqual(views[0].qualified_name, "views.document_counts")
        self.assertEqual(views[0].sql, "SELECT CAST(1 AS BIGINT) AS n")
        self.assertEqual(views[0].columns, ("n",))
        self.assertEqual(views[0].column_types, ("BIGINT",))
        self.assertIn(
            "duckdb_views()",
            catalogue.trusted_remote_rows.call_args_list[0].args[0],
        )
        self.assertEqual(
            catalogue.trusted_remote_rows.call_args_list[1].args[0],
            'DESCRIBE "atlas"."views"."document_counts"',
        )

    def test_view_create_uses_managed_remote_sql(self) -> None:
        catalogue = _catalogue()
        store = CatalogueViewStore(catalogue)
        created = DuckLakeView(
            view_uuid=uuid4(),
            schema_name="views",
            view_name="document_counts",
            sql="SELECT 1 AS n",
            columns=("n",),
        )
        with patch.object(store, "list", side_effect=[[], [created]]):
            result = store.create(name="document_counts", sql="SELECT 1 AS n")

        self.assertIs(result, created)
        catalogue.trusted_remote_execute.assert_called_once_with(
            'CREATE VIEW "atlas"."views"."document_counts" AS SELECT 1 AS n'
        )

    def test_scalar_macro_create_uses_managed_remote_sql(self) -> None:
        catalogue = _catalogue()
        store = CatalogueScalarMacroStore(catalogue)
        created = DuckLakeScalarMacro(
            schema_name="macros",
            macro_name="increment",
            parameters=("value",),
        )
        with patch.object(store, "get", side_effect=[None, created]):
            result = store.create(
                name="increment",
                parameters=["value"],
                sql="value + 1",
            )

        self.assertIs(result, created)
        catalogue.trusted_remote_execute.assert_called_once_with(
            'CREATE MACRO "atlas"."macros"."increment"("value") AS (value + 1)'
        )

    def test_table_macro_replace_preserves_optional_default(self) -> None:
        catalogue = _catalogue()
        store = CatalogueTableMacroStore(catalogue)
        replaced = DuckLakeTableMacro(
            schema_name="macros",
            macro_name="numbers_between",
            parameters=("minimum", "maximum"),
        )
        with patch.object(store, "get", return_value=replaced):
            result = store.replace(
                name="numbers_between",
                parameters=["minimum", "maximum"],
                parameter_defaults={"maximum": "4"},
                sql="SELECT value FROM range(maximum) AS values(value)",
            )

        self.assertIs(result, replaced)
        catalogue.trusted_remote_execute.assert_called_once_with(
            'CREATE OR REPLACE MACRO "atlas"."macros"."numbers_between"'
            '("minimum", "maximum" := 4) AS TABLE '
            "(SELECT value FROM RANGE(0, maximum) AS values(value))"
        )


class ManagedFixtureSeedingTests(unittest.TestCase):
    def test_full_seed_routes_every_definition_through_managed_stores(self) -> None:
        catalogue = _catalogue()
        session = MagicMock()
        fixture = RelationFixture(
            fixture_path="fixture.sql",
            name="fixture",
            sql="SELECT 1",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for directory in (
                "queries",
                "views",
                "materialized_views/url",
                "table_macros",
            ):
                path = root / directory
                path.mkdir(parents=True)
                (path / "fixture.sql").write_text("SELECT 1", encoding="utf-8")

            with (
                patch.object(
                    catalogue_fixtures,
                    "seed_system_catalogue_fixtures",
                ) as seed_system,
                patch.object(
                    catalogue_fixtures,
                    "_parse_query_fixture",
                    return_value=fixture,
                ),
                patch.object(
                    catalogue_fixtures,
                    "_parse_relation_fixture",
                    return_value=fixture,
                ),
                patch.object(catalogue_fixtures, "_seed_query") as seed_query,
                patch.object(catalogue_fixtures, "_seed_view") as seed_view,
                patch.object(
                    catalogue_fixtures,
                    "_seed_materialization",
                ) as seed_materialization,
                patch.object(catalogue_fixtures, "_seed_macro") as seed_macro,
                patch.object(catalogue_fixtures, "_drop_retired_macros"),
            ):
                seed_catalogue_fixtures(session, catalogue, root)

        seed_system.assert_called_once_with(session, catalogue, root)
        seed_query.assert_called_once()
        self.assertEqual(seed_view.call_count, 2)
        seed_materialization.assert_called_once()
        seed_macro.assert_called_once()
        catalogue.validate_schema.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
