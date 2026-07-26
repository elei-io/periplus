from __future__ import annotations

import unittest

from atlas_sql import (
    AtlasCompiler,
    GraphEdgePurpose,
    ScalarMacroDefinition,
)
from catalogue.compiler import (
    SqlCompilationOutcome,
    SqlCompilationPurpose,
)
from runtime.edge_sql import (
    compile_edge_sql,
    edge_uses_catalogue,
    freeze_edge_sql,
)


class GraphEdgeCompilerTests(unittest.TestCase):
    def test_valid_edge_returns_typed_executable_decision(self) -> None:
        sql = (
            "SELECT target_url AS url FROM edge.page_links "
            "WHERE crawl_id = $crawl_id LIMIT 25"
        )

        result = AtlasCompiler.embedded().compile(
            sql,
            purpose=GraphEdgePurpose(),
        )

        self.assertEqual(result.purpose, SqlCompilationPurpose.GRAPH_EDGE)
        self.assertIn(
            result.outcome,
            {
                SqlCompilationOutcome.UNCHANGED,
                SqlCompilationOutcome.OPTIMIZED,
            },
        )
        self.assertTrue(result.valid)
        self.assertIsNotNone(result.executable_sql)

    def test_contract_failures_are_invalid_structured_diagnostics(self) -> None:
        cases = {
            "SELECT target_url AS url FROM edge.page_links LIMIT 10": (
                "edge_crawl_id_required"
            ),
            (
                "SELECT target_url AS url FROM edge.page_links "
                "WHERE crawl_id = $crawl_id"
            ): "edge_literal_limit_required",
            (
                "SELECT target_url FROM edge.page_links "
                "WHERE crawl_id = $crawl_id LIMIT 10"
            ): "edge_url_output_required",
            (
                "SELECT url FROM crawls "
                "WHERE crawl_id = $crawl_id LIMIT 10"
            ): "edge_page_links_required",
            (
                "SELECT p.target_url AS url FROM edge.page_links AS p, "
                "read_csv_auto('/etc/passwd') AS leaked "
                "WHERE p.crawl_id = $crawl_id LIMIT 10"
            ): "edge_unmanaged_relation",
        }
        compiler = AtlasCompiler.embedded()

        for sql, code in cases.items():
            with self.subTest(code=code):
                result = compiler.compile(sql, purpose=GraphEdgePurpose())
                self.assertEqual(
                    result.outcome,
                    SqlCompilationOutcome.INVALID,
                )
                self.assertIsNone(result.executable_sql)
                self.assertEqual(result.diagnostics[-1].code, code)
                self.assertEqual(
                    result.diagnostics[-1].documentation_anchor,
                    "graph-edge-query",
                )

    def test_invalid_edge_cannot_receive_authored_fallback(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "outer literal LIMIT",
        ):
            compile_edge_sql(
                "SELECT target_url AS url FROM edge.page_links "
                "WHERE crawl_id = $crawl_id"
            )

    def test_valid_unresolved_macro_degrades_only_after_contract_proof(
        self,
    ) -> None:
        sql = (
            "SELECT macros.choose_url(target_url) AS url "
            "FROM edge.page_links WHERE crawl_id = $crawl_id LIMIT 10"
        )

        unresolved = AtlasCompiler.embedded().compile(
            sql,
            purpose=GraphEdgePurpose(),
        )
        resolved = AtlasCompiler.embedded().compile(
            sql,
            purpose=GraphEdgePurpose(
                scalar_macros=(
                    ScalarMacroDefinition(
                        schema_name="macros",
                        macro_name="choose_url",
                        parameters=("value",),
                        sql="value",
                    ),
                ),
            ),
        )

        self.assertEqual(
            unresolved.outcome,
            SqlCompilationOutcome.UNSUPPORTED,
        )
        self.assertTrue(unresolved.valid)
        self.assertEqual(unresolved.executable_sql, sql)
        self.assertTrue(resolved.supported)
        self.assertIn("target_url AS url", resolved.executable_sql or "")

    def test_page_only_and_historical_classification_uses_compiler_analysis(
        self,
    ) -> None:
        self.assertFalse(
            edge_uses_catalogue(
                "WITH links AS ("
                "SELECT * FROM edge.page_links"
                ") SELECT target_url AS url FROM links "
                "WHERE crawl_id = $crawl_id LIMIT 10"
            )
        )
        self.assertTrue(
            edge_uses_catalogue(
                "SELECT p.target_url AS url FROM edge.page_links AS p "
                "JOIN views.previous_pages AS h USING (document_id) "
                "WHERE p.crawl_id = $crawl_id LIMIT 10"
            )
        )
        self.assertTrue(
            edge_uses_catalogue(
                "SELECT macros.choose_url(target_url) AS url "
                "FROM edge.page_links "
                "WHERE crawl_id = $crawl_id LIMIT 10"
            )
        )

    def test_comments_and_literals_do_not_duplicate_crawl_binding(self) -> None:
        for sql in (
            "SELECT target_url AS url FROM edge.page_links "
            "WHERE crawl_id = $crawl_id -- bind $crawl_id here\nLIMIT 10",
            "SELECT target_url AS url FROM edge.page_links "
            "WHERE crawl_id = $crawl_id AND '$crawl_id' <> '' LIMIT 10",
        ):
            with self.subTest(sql=sql):
                result = AtlasCompiler.embedded().compile(
                    sql,
                    purpose=GraphEdgePurpose(),
                )
                self.assertTrue(result.valid)

    def test_frozen_edge_classification_uses_expanded_sql(
        self,
    ) -> None:
        frozen = freeze_edge_sql(
            "SELECT macros.choose_url(target_url) AS url "
            "FROM edge.page_links "
            "WHERE crawl_id = $crawl_id LIMIT 10",
            purpose=GraphEdgePurpose(
                scalar_macros=(
                    ScalarMacroDefinition(
                        schema_name="macros",
                        macro_name="choose_url",
                        parameters=("value",),
                        sql="value",
                    ),
                ),
            ),
            catalogue_revision="revision-9",
        )

        self.assertNotIn("macros.choose_url", frozen.executable_sql)
        self.assertFalse(frozen.uses_catalogue)
        self.assertEqual(frozen.catalogue_revision, "revision-9")

        historical = freeze_edge_sql(
            "SELECT p.target_url AS url FROM edge.page_links AS p "
            "JOIN views.previous AS h USING (document_id) "
            "WHERE p.crawl_id = $crawl_id LIMIT 10",
            catalogue_revision="revision-9",
        )
        self.assertTrue(historical.uses_catalogue)
        self.assertEqual(historical.catalogue_revision, "revision-9")

    def test_single_compilation_may_copy_the_crawl_predicate(self) -> None:
        sql = (
            "WITH links AS ("
            "SELECT crawl_id, target_url FROM edge.page_links"
            ") "
            "SELECT target_url AS url FROM links "
            "WHERE crawl_id = $crawl_id LIMIT 10"
        )

        executable = compile_edge_sql(sql)

        self.assertEqual(executable.count("$crawl_id"), 2)
        self.assertIn("WITH links AS", executable)


if __name__ == "__main__":
    unittest.main()
