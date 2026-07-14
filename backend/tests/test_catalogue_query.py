from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import pyarrow as pa
from ducklake_client import DiskStorage, DuckDBCatalog
from fastapi import HTTPException

from api.routers.catalogue import CatalogueQueryMode, CatalogueSqlRequest, sql_query
from api.catalogue_pool import CatalogueReadPool, CatalogueReadPoolExhausted
from repository.catalogue import Catalogue, CatalogueConfig
from repository.catalogue.query import (
    CatalogueQueryError,
    classify_select,
    execute_arrow_query,
    explain_arrow_query,
    lint_select,
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
    def test_warns_when_dom_helper_is_not_fed_by_bounded_materialized_cte(self) -> None:
        diagnostics = lint_select(
            "SELECT inner_html(e.document_id, e.element_index) "
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
            SELECT inner_html(document_id, element_index)
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

    def test_reports_invalid_css_select_before_execution(self) -> None:
        diagnostics = lint_select(
            "SELECT * FROM elements e WHERE e.document_id='doc' "
            "AND css_select(':hover')"
        )
        self.assertEqual([item.code for item in diagnostics], ["invalid_css_select"])
        self.assertEqual(diagnostics[0].severity, "error")

    def test_unbounded_structural_css_is_advisory_but_row_local_css_is_not(self) -> None:
        self.assertEqual(
            lint_select(
                "SELECT * FROM elements WHERE css_select('a') LIMIT 100"
            ),
            [],
        )
        diagnostics = lint_select(
            "SELECT * FROM elements WHERE css_select('article > a') LIMIT 100"
        )
        self.assertEqual(
            [item.code for item in diagnostics],
            ["unbounded_structural_css"],
        )
        self.assertEqual(diagnostics[0].severity, "warning")

        self.assertNotIn(
            "unbounded_structural_css",
            [
                item.code
                for item in lint_select(
                    "SELECT * FROM elements e WHERE e.document_id='doc' "
                    "AND css_select('article > a') LIMIT 100"
                )
            ],
        )


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

    def test_css_select_is_rewritten_at_the_catalogue_execution_boundary(self) -> None:
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
                    SELECT count(*) AS matches
                    FROM elements e
                    WHERE e.document_id = $document_id
                      AND css_select('article > a[href]')
                    """,
                    {"document_id": "missing"},
                )
                payload = b"".join(stream_arrow_reader(reader))

        result = pa.ipc.open_stream(payload).read_all()
        self.assertEqual(result.to_pylist(), [{"matches": 0}])

    def test_unbounded_row_local_css_runs_through_the_api_execution_path(self) -> None:
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
                    "SELECT get_attribute('href'), readable_text() "
                    "FROM elements WHERE css_select('a') LIMIT 100",
                )
                payload = b"".join(stream_arrow_reader(reader))

        self.assertEqual(pa.ipc.open_stream(payload).read_all().num_rows, 0)

    def test_invalid_css_select_is_a_catalogue_query_error(self) -> None:
        catalogue = MagicMock(spec=Catalogue)
        with self.assertRaisesRegex(CatalogueQueryError, "browser state"):
            execute_arrow_query(
                catalogue,
                "SELECT * FROM elements e WHERE e.document_id='doc' "
                "AND css_select(':hover')",
            )

    @patch("api.routers.catalogue.execute_arrow_query")
    def test_duckdb_errors_are_returned_before_streaming_starts(
        self,
        execute_query: MagicMock,
    ) -> None:
        request = MagicMock()
        pool = request.app.state.catalogue_read_pool
        catalogue = pool.acquire.return_value
        execute_query.side_effect = duckdb.CatalogException(
            "Catalog Error: Table with name missing does not exist!"
        )

        with self.assertRaises(HTTPException) as raised:
            sql_query(CatalogueSqlRequest(sql="SELECT * FROM missing"), request)

        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(
            raised.exception.detail,
            "Catalog Error: Table with name missing does not exist!",
        )
        pool.release.assert_called_once_with(catalogue)

    @patch("api.routers.catalogue.explain_arrow_query")
    def test_api_dispatches_explain_modes(self, explain_query: MagicMock) -> None:
        request = MagicMock()
        catalogue = request.app.state.catalogue_read_pool.acquire.return_value
        explain_query.return_value = MagicMock()

        sql_query(
            CatalogueSqlRequest(sql="SELECT 1", mode=CatalogueQueryMode.EXPLAIN),
            request,
        )
        explain_query.assert_called_once_with(catalogue, "SELECT 1", analyze=False)

        explain_query.reset_mock()
        sql_query(
            CatalogueSqlRequest(
                sql="SELECT 1",
                mode=CatalogueQueryMode.EXPLAIN_ANALYZE,
            ),
            request,
        )
        explain_query.assert_called_once_with(catalogue, "SELECT 1", analyze=True)

    def test_request_defaults_to_run_mode(self) -> None:
        self.assertEqual(CatalogueSqlRequest(sql="SELECT 1").mode, CatalogueQueryMode.RUN)

    @patch("api.routers.catalogue.execute_arrow_query")
    def test_css_rewrite_errors_are_returned_before_streaming_starts(
        self,
        execute_query: MagicMock,
    ) -> None:
        request = MagicMock()
        pool = request.app.state.catalogue_read_pool
        catalogue = pool.acquire.return_value
        execute_query.side_effect = CatalogueQueryError(
            "pseudo-class :hover depends on browser runtime state"
        )

        with self.assertRaises(HTTPException) as raised:
            sql_query(
                CatalogueSqlRequest(
                    sql="SELECT * FROM elements e WHERE e.document_id='doc' "
                    "AND css_select(':hover')"
                ),
                request,
            )

        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn(":hover", raised.exception.detail)
        pool.release.assert_called_once_with(catalogue)

    def test_pool_wait_timeout_is_returned_as_service_unavailable(self) -> None:
        request = MagicMock()
        pool = request.app.state.catalogue_read_pool
        pool.acquire.side_effect = CatalogueReadPoolExhausted(
            "catalogue SQL is busy; no read connection became available within 5 seconds"
        )

        with self.assertRaises(HTTPException) as raised:
            sql_query(CatalogueSqlRequest(sql="SELECT 1"), request)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            raised.exception.detail,
            "catalogue SQL is busy; no read connection became available within 5 seconds",
        )


class CatalogueReadPoolTests(unittest.TestCase):
    def test_connections_are_validated_once_and_reused(self) -> None:
        catalogues = [MagicMock(spec=Catalogue), MagicMock(spec=Catalogue)]
        pending = iter(catalogues)
        pool = CatalogueReadPool(
            2,
            threads=2,
            wait_timeout_seconds=1,
            factory=lambda: next(pending),
        )

        pool.open()
        first = pool.acquire()
        pool.release(first)
        reused = pool.acquire()
        pool.release(reused)
        pool.close()

        self.assertIn(first, catalogues)
        self.assertIn(reused, catalogues)
        for catalogue in catalogues:
            catalogue.connection.execute.assert_called_once_with("SET threads = 2")
            catalogue.validate_schema.assert_called_once_with()
            catalogue.close.assert_called_once_with()

    def test_acquire_times_out_when_every_connection_is_leased(self) -> None:
        catalogue = MagicMock(spec=Catalogue)
        pool = CatalogueReadPool(
            1,
            threads=2,
            wait_timeout_seconds=0.01,
            factory=lambda: catalogue,
        )
        pool.open()
        leased = pool.acquire()

        with self.assertRaisesRegex(
            CatalogueReadPoolExhausted,
            "no read connection became available",
        ):
            pool.acquire()

        pool.release(leased)
        pool.close()


if __name__ == "__main__":
    unittest.main()
