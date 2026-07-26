from __future__ import annotations

import unittest

from atlas_sql import (
    AtlasCompiler,
    CatalogueDefinitionPurpose,
    CompilationOutcome,
    ScalarMacroDefinition,
    TableMacroDefinition,
    ViewDefinition,
)


class CatalogueDefinitionCompilerTests(unittest.TestCase):
    def test_view_reports_transitive_definition_paths(self) -> None:
        result = AtlasCompiler.embedded(
            catalogue_revision="revision-1"
        ).compile(
            "SELECT macros.clean(value) FROM macros.rows(1)",
            purpose=CatalogueDefinitionPurpose(
                kind="view",
                schema_name="views",
                object_name="example",
                scalar_macros=(
                    ScalarMacroDefinition(
                        schema_name="macros",
                        macro_name="clean",
                        parameters=("value",),
                        sql="lower(value)",
                    ),
                ),
                table_macros=(
                    TableMacroDefinition(
                        schema_name="macros",
                        macro_name="rows",
                        parameters=("value",),
                        parameter_defaults=(),
                        sql="SELECT value FROM views.source",
                    ),
                ),
                views=(
                    ViewDefinition(
                        schema_name="views",
                        view_name="source",
                        sql="SELECT 1 AS value",
                    ),
                ),
            ),
        )

        self.assertEqual(result.outcome, CompilationOutcome.UNCHANGED)
        self.assertEqual(
            {
                (item.qualified_name, item.path)
                for item in result.dependencies
            },
            {
                (
                    "macros.clean",
                    ("views.example", "macros.clean"),
                ),
                (
                    "macros.rows",
                    ("views.example", "macros.rows"),
                ),
                (
                    "views.source",
                    ("views.example", "macros.rows", "views.source"),
                ),
            },
        )
        self.assertEqual(result.catalogue_revision, "revision-1")

    def test_scalar_macro_expression_is_valid_definition_sql(self) -> None:
        result = AtlasCompiler.embedded().compile(
            "coalesce(value, 0)",
            purpose=CatalogueDefinitionPurpose(
                kind="scalar_macro",
                schema_name="macros",
                object_name="default_value",
                parameters=("value",),
            ),
        )
        self.assertTrue(result.valid)
        self.assertEqual(result.outcome, CompilationOutcome.UNCHANGED)
        self.assertEqual(result.executable_sql, "coalesce(value, 0)")

    def test_unresolved_catalogue_macro_is_advisory_unsupported(self) -> None:
        result = AtlasCompiler.embedded().compile(
            "SELECT macros.missing(value)",
            purpose=CatalogueDefinitionPurpose(
                kind="view",
                schema_name="views",
                object_name="example",
            ),
        )
        self.assertTrue(result.valid)
        self.assertEqual(result.outcome, CompilationOutcome.UNSUPPORTED)
        self.assertEqual(result.executable_sql, "SELECT macros.missing(value)")


if __name__ == "__main__":
    unittest.main()
