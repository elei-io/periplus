from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb
import pyarrow as pa
from ducklake_client import DiskStorage, DuckDBCatalog

from api.routers.catalogue import (
    CatalogueSqlRequest,
    lint_sql,
    prepare_sql,
)
from tests.catalogue_helpers import seed_system_macros
from repository.catalogue import Catalogue, CatalogueConfig
from repository.catalogue.query import (
    CatalogueQueryError,
    CatalogueStatementKind,
    classify_catalogue_statement,
    classify_select,
    execute_arrow_query,
    explain_arrow_query,
    lint_select,
    prepare_catalogue_query,
    stream_arrow_reader,
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
        self.assertEqual(response.diagnostics[0].severity, "warning")

    def test_prepare_endpoint_returns_validated_canonical_statement(self) -> None:
        response = prepare_sql(CatalogueSqlRequest(sql=" EXPLAIN ANALYZE SELECT 1; "))

        self.assertEqual(response.sql, "SELECT 1;")
        self.assertEqual(response.statement_kind, "explain_analyze")

    def test_warns_when_dom_helper_is_not_fed_by_bounded_materialized_cte(self) -> None:
        diagnostics = lint_select(
            "SELECT macros.inner_html(e.document_id, e.element_index) "
            "FROM elements e LIMIT 100"
        )
        self.assertEqual([item.code for item in diagnostics], ["unbounded_dom_helper"])

    def test_accepts_dom_helper_from_bounded_materialized_cte(self) -> None:
        diagnostics = lint_select(
            """
            WITH selected AS MATERIALIZED (
                SELECT document_id, element_index
                FROM elements
                LIMIT 100
            )
            SELECT macros.inner_html(document_id, element_index)
            FROM selected
            """
        )
        self.assertEqual(diagnostics, [])

    def test_warns_for_absurd_limit(self) -> None:
        diagnostics = lint_select("SELECT * FROM elements LIMIT 1000000")
        self.assertEqual([item.code for item in diagnostics], ["absurd_limit"])

    def test_warns_for_missing_limit_but_not_single_aggregate(self) -> None:
        self.assertEqual(
            [item.code for item in lint_select("SELECT * FROM crawls")],
            ["missing_limit"],
        )
        self.assertEqual(lint_select("SELECT count(*) FROM crawls"), [])

    def test_incomplete_sql_has_no_transient_lint_diagnostics(self) -> None:
        self.assertEqual(lint_select("SELECT * FROM"), [])


class CatalogueQueryExecutionTests(unittest.TestCase):
    def test_explain_does_not_execute_but_explain_analyze_does(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                planned = explain_arrow_query(
                    catalogue,
                    "SELECT error('query executed')",
                ).read_all()
                with self.assertRaisesRegex(duckdb.Error, "query executed"):
                    explain_arrow_query(
                        catalogue,
                        "SELECT error('query executed')",
                        analyze=True,
                    )

        self.assertEqual(planned.column_names, ["explain_key", "explain_value"])
        self.assertEqual(planned.num_rows, 1)

    def test_unqualified_managed_tables_resolve_in_catalogue_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                reader = execute_arrow_query(
                    catalogue,
                    """
                    SELECT count(*) AS element_count
                    FROM elements e
                    JOIN documents d ON d.document_id = e.document_id
                    JOIN crawls c ON c.document_id = e.document_id
                    """,
                )
                payload = b"".join(stream_arrow_reader(reader))

        result = pa.ipc.open_stream(payload).read_all()
        self.assertEqual(result.to_pylist(), [{"element_count": 0}])

    def test_canonical_dom_helpers_run_through_the_api_execution_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                seed_system_macros(catalogue)
                reader = execute_arrow_query(
                    catalogue,
                    "SELECT macros.get_attribute(attributes, 'href'), "
                    "macros.readable_text(document_id, element_index) "
                    "FROM elements LIMIT 100",
                )
                payload = b"".join(stream_arrow_reader(reader))

        self.assertEqual(pa.ipc.open_stream(payload).read_all().num_rows, 0)

    def test_query_preparation_does_not_rewrite_submitted_sql(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                sql = (
                    "SELECT macros.readable_text(document_id, element_index)\n"
                    "FROM elements LIMIT $limit"
                )
                prepared = prepare_catalogue_query(
                    catalogue,
                    sql,
                    {"limit": 100},
                )

        self.assertEqual(prepared.sql, sql)

    def test_native_explain_classification_validates_the_inner_query(self) -> None:
        explained = classify_catalogue_statement("EXPLAIN SELECT 1")
        analyzed = classify_catalogue_statement("EXPLAIN ANALYZE SELECT 1")

        self.assertEqual(explained.kind, CatalogueStatementKind.EXPLAIN)
        self.assertEqual(explained.sql, "SELECT 1")
        self.assertEqual(analyzed.kind, CatalogueStatementKind.EXPLAIN_ANALYZE)
        self.assertEqual(analyzed.sql, "SELECT 1")
        with self.assertRaises(CatalogueQueryError):
            classify_catalogue_statement("EXPLAIN DELETE FROM documents")


if __name__ == "__main__":
    unittest.main()
