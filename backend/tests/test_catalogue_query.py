from __future__ import annotations

import unittest
from types import SimpleNamespace

from api.routers.catalogue import CatalogueSqlRequest, lint_sql
from repository.catalogue.query import (
    CatalogueQueryError,
    CatalogueStatementKind,
    classify_catalogue_statement,
    classify_select,
    lint_select,
    prepare_catalogue_query,
    validate_interactive_catalogue_statement,
)


class CatalogueQueryClassificationTests(unittest.TestCase):
    def test_accepts_select_and_cte_queries(self) -> None:
        classify_select("SELECT * FROM atlas.main.documents")
        classify_select("WITH ids AS (SELECT 1 AS id) SELECT * FROM ids")

    def test_rejects_non_select_statements(self) -> None:
        for sql in (
            "DELETE FROM atlas.main.documents",
            "CREATE TABLE danger (id INTEGER)",
            "COPY (SELECT 1) TO '/tmp/result.csv'",
            "PRAGMA version",
            "ATTACH 'other.db' AS other",
        ):
            with self.subTest(sql=sql), self.assertRaises(CatalogueQueryError):
                classify_select(sql)

    def test_rejects_multiple_and_invalid_statements(self) -> None:
        with self.assertRaises(CatalogueQueryError):
            classify_select("SELECT 1; SELECT 2")
        with self.assertRaises(CatalogueQueryError):
            classify_select("SELECT FROM")


class CatalogueQueryLintTests(unittest.TestCase):
    def test_lint_endpoint_reports_unbounded_interactive_query(self) -> None:
        response = lint_sql(CatalogueSqlRequest(sql="SELECT * FROM documents"))
        self.assertEqual(
            [diagnostic.code for diagnostic in response.diagnostics],
            ["missing_limit"],
        )

    def test_dom_helper_requires_a_bounded_materialized_cte(self) -> None:
        self.assertEqual(
            [
                item.code
                for item in lint_select(
                    "SELECT macros.inner_html(e.document_id, e.element_index) "
                    "FROM elements e LIMIT 100"
                )
            ],
            ["unbounded_dom_helper"],
        )
        self.assertEqual(
            lint_select(
                """
                WITH selected AS MATERIALIZED (
                    SELECT document_id, element_index FROM elements LIMIT 100
                )
                SELECT macros.inner_html(document_id, element_index)
                FROM selected
                """
            ),
            [],
        )

    def test_limits_and_single_aggregates_are_linted(self) -> None:
        self.assertEqual(
            [item.code for item in lint_select("SELECT * FROM elements LIMIT 1000000")],
            ["absurd_limit"],
        )
        self.assertEqual(
            [item.code for item in lint_select("SELECT * FROM crawls")],
            ["missing_limit"],
        )
        self.assertEqual(lint_select("SELECT count(*) FROM crawls"), [])


class CatalogueQueryContractTests(unittest.TestCase):
    def test_query_preparation_preserves_sql_and_binds_parameters(self) -> None:
        catalogue = SimpleNamespace(
            config=SimpleNamespace(alias="atlas", schema="main")
        )
        sql = "SELECT * FROM elements LIMIT $limit"

        prepared = prepare_catalogue_query(catalogue, sql, {"limit": 100})

        self.assertEqual(prepared.sql, sql)
        self.assertEqual(prepared.bindings, {"limit": 100})
        self.assertEqual(prepared.namespace, '"atlas"."main"')

    def test_native_explain_classification_validates_the_inner_query(self) -> None:
        explained = classify_catalogue_statement("EXPLAIN SELECT 1")
        analyzed = classify_catalogue_statement("EXPLAIN ANALYZE SELECT 1")
        self.assertEqual(explained.kind, CatalogueStatementKind.EXPLAIN)
        self.assertEqual(analyzed.kind, CatalogueStatementKind.EXPLAIN_ANALYZE)
        with self.assertRaises(CatalogueQueryError):
            classify_catalogue_statement("EXPLAIN DELETE FROM documents")

    def test_interactive_queries_are_confined_to_public_catalogue_reads(self) -> None:
        validate_interactive_catalogue_statement(
            "SELECT * FROM documents",
            catalogue_alias="atlas",
            catalogue_schema="main",
        )
        rejected = (
            "SELECT * FROM read_csv_auto('/etc/passwd')",
            "SELECT query('DELETE FROM documents')",
            "SELECT * FROM duckdb_secrets()",
            "SELECT * FROM other.main.documents",
            "SELECT * FROM atlas.pg_catalog.pg_tables",
        )
        for sql in rejected:
            with self.subTest(sql=sql), self.assertRaises(CatalogueQueryError):
                validate_interactive_catalogue_statement(
                    sql,
                    catalogue_alias="atlas",
                    catalogue_schema="main",
                )


if __name__ == "__main__":
    unittest.main()
