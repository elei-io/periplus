from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from api.routers.catalogue import (
    CatalogueCompileRequest,
    CatalogueSqlRequest,
    compile_sql,
    compile_sql_result,
)
from catalogue.compiler import (
    InteractiveQueryPurpose,
    TableMacroDefinition,
)
from tests.test_catalogue_compiler_physical import _dom_metadata
from repository.catalogue.interactive import prepare_interactive_query
from repository.catalogue.compiler_definitions import (
    CatalogueCompilerDefinitions,
)
from repository.catalogue.quack_runtime import CatalogueQueryExecutionError
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
            classify_select("SELECT FROM documents")


class CatalogueQueryLintTests(unittest.TestCase):
    def test_lint_endpoint_expands_authoritative_table_macro_definition(
        self,
    ) -> None:
        response = asyncio.run(
            compile_sql(
                CatalogueCompileRequest(
                    sql=(
                        "SELECT * "
                        "FROM macros.suggest_records('%toscrape%') "
                        "LIMIT 10"
                    )
                ),
                CatalogueCompilerDefinitions(
                    revision="lake-1",
                    scalar_macros=(),
                    table_macros=(
                        TableMacroDefinition(
                            schema_name="macros",
                            macro_name="suggest_records",
                            parameters=("p_url",),
                            parameter_defaults=(),
                            sql="SELECT p_url AS pattern",
                        ),
                    ),
                    views=(),
                    scalar_functions=(),
                ),
            )
        )

        self.assertTrue(response.valid)
        self.assertTrue(response.supported)
        self.assertEqual(response.outcome, "optimized")
        self.assertNotIn(
            "unsupported_function",
            [diagnostic.code for diagnostic in response.diagnostics],
        )

    def test_lint_endpoint_reports_unbounded_interactive_query(self) -> None:
        response = compile_sql_result(
            CatalogueSqlRequest(sql="SELECT * FROM documents")
        )
        self.assertTrue(response.valid)
        self.assertTrue(response.supported)
        self.assertEqual(response.outcome, "unchanged")
        self.assertEqual(
            [diagnostic.code for diagnostic in response.diagnostics],
            ["missing_limit"],
        )

    def test_lint_endpoint_blocks_invalid_but_not_unsupported_sql(
        self,
    ) -> None:
        invalid = compile_sql_result(
            CatalogueSqlRequest(sql="SELECT FROM documents")
        )
        self.assertFalse(invalid.valid)
        self.assertFalse(invalid.supported)
        self.assertEqual(invalid.outcome, "invalid")
        self.assertEqual(invalid.diagnostics[-1].severity, "error")

        unsupported = compile_sql_result(
            CatalogueSqlRequest(
                sql="SELECT macros.installed(title) FROM documents"
            )
        )
        self.assertTrue(unsupported.valid)
        self.assertFalse(unsupported.supported)
        self.assertEqual(unsupported.outcome, "unsupported")
        self.assertEqual(
            unsupported.diagnostics[-1].code,
            "unsupported_function",
        )
        self.assertEqual(
            unsupported.diagnostics[-1].severity,
            "warning",
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

class InteractiveQueryCompilationTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_boundary_rejects_an_unbounded_element_scan(self) -> None:
        runtime = SimpleNamespace(
            config=SimpleNamespace(
                catalogue_alias="atlas",
                catalogue_schema="main",
            ),
            query_bucket=object(),
            preflight=AsyncMock(),
            prepare=AsyncMock(),
        )

        with self.assertRaisesRegex(
            CatalogueQueryError,
            "must constrain document_id",
        ):
            await prepare_interactive_query(
                runtime,  # type: ignore[arg-type]
                (
                    "SELECT attributes FROM elements "
                    "WHERE tag = 'article' LIMIT 100"
                ),
                purpose=InteractiveQueryPurpose(metadata=_dom_metadata()),
            )

        runtime.prepare.assert_not_awaited()

    async def test_public_boundary_executes_a_direct_bounded_element_scan(
        self,
    ) -> None:
        runtime = SimpleNamespace(
            config=SimpleNamespace(
                catalogue_alias="atlas",
                catalogue_schema="main",
            ),
            query_bucket=object(),
            preflight=AsyncMock(),
            prepare=AsyncMock(return_value=object()),
        )
        create_query = AsyncMock()
        sql = "SELECT * FROM elements LIMIT 10"

        with patch(
            "repository.catalogue.interactive.create_catalogue_query",
            new=create_query,
        ):
            await prepare_interactive_query(
                runtime,  # type: ignore[arg-type]
                sql,
                purpose=InteractiveQueryPurpose(metadata=_dom_metadata()),
            )

        compilation = runtime.prepare.await_args.kwargs["compilation"]
        self.assertEqual(compilation.executable_sql, sql)
        self.assertTrue(compilation.supported)

    async def test_public_boundary_executes_expanded_stored_table_macro(
        self,
    ) -> None:
        runtime = SimpleNamespace(
            config=SimpleNamespace(
                catalogue_alias="atlas",
                catalogue_schema="main",
            ),
            query_bucket=object(),
            preflight=AsyncMock(),
            prepare=AsyncMock(return_value=object()),
        )
        purpose = InteractiveQueryPurpose(
            table_macros=(
                TableMacroDefinition(
                    schema_name="macros",
                    macro_name="suggest_records",
                    parameters=("p_url",),
                    parameter_defaults=(),
                    sql="SELECT p_url AS pattern",
                ),
            )
        )
        create_query = AsyncMock()

        with patch(
            "repository.catalogue.interactive.create_catalogue_query",
            new=create_query,
        ):
            await prepare_interactive_query(
                runtime,  # type: ignore[arg-type]
                "SELECT * FROM macros.suggest_records('%toscrape%') LIMIT 10",
                purpose=purpose,
            )

        executed_sql = (
            runtime.prepare.await_args.kwargs["compilation"].executable_sql
        )
        self.assertIsNotNone(executed_sql)
        self.assertNotIn("suggest_records", executed_sql.lower())
        self.assertIn("'%toscrape%'", executed_sql)
        state = create_query.await_args.args[1]
        self.assertEqual(state.optimization_status, "optimized")
        self.assertNotIn(
            "unsupported_function",
            [
                diagnostic.code
                for diagnostic in state.optimization_diagnostics
            ],
        )

    async def test_public_boundary_executes_compiled_interactive_sql(
        self,
    ) -> None:
        active = object()
        runtime = SimpleNamespace(
            config=SimpleNamespace(
                catalogue_alias="atlas",
                catalogue_schema="main",
            ),
            query_bucket=object(),
            preflight=AsyncMock(),
            prepare=AsyncMock(return_value=active),
        )
        sql = (
            "SELECT document_id, upper(title) AS first_title, "
            "  upper(title) AS second_title "
            "FROM documents "
            "ORDER BY document_id, first_title, second_title"
        )
        create_query = AsyncMock()
        with patch(
            "repository.catalogue.interactive.create_catalogue_query",
            new=create_query,
        ):
            _query_id, statement, prepared = await prepare_interactive_query(
                runtime,  # type: ignore[arg-type]
                sql,
            )

        self.assertIs(prepared, active)
        self.assertEqual(statement.sql, sql)
        executed_sql = (
            runtime.prepare.await_args.kwargs["compilation"].executable_sql
        )
        self.assertIsNotNone(executed_sql)
        self.assertIn("CROSS JOIN LATERAL", executed_sql)
        self.assertEqual(executed_sql.count("UPPER("), 1)
        state = create_query.await_args.args[1]
        self.assertEqual(state.optimization_status, "optimized")
        self.assertEqual(
            [rewrite.rule for rewrite in state.applied_rewrites],
            ["repeated_scalar_expression"],
        )
        self.assertIn(
            "deterministic",
            state.applied_rewrites[0].evidence,
        )
        self.assertEqual(
            [diagnostic.code for diagnostic in state.optimization_diagnostics],
            ["missing_limit"],
        )
        self.assertEqual(
            state.optimization_diagnostics[0].severity,
            "warning",
        )

    async def test_public_boundary_falls_back_to_authored_valid_sql(
        self,
    ) -> None:
        active = object()
        runtime = SimpleNamespace(
            config=SimpleNamespace(
                catalogue_alias="atlas",
                catalogue_schema="main",
            ),
            query_bucket=object(),
            preflight=AsyncMock(),
            prepare=AsyncMock(return_value=active),
        )
        sql = "SELECT macros.installed(title) AS title FROM documents"
        create_query = AsyncMock()
        with patch(
            "repository.catalogue.interactive.create_catalogue_query",
            new=create_query,
        ):
            _query_id, statement, _prepared = await prepare_interactive_query(
                runtime,  # type: ignore[arg-type]
                sql,
            )

        self.assertEqual(statement.sql, sql)
        self.assertEqual(
            runtime.prepare.await_args.kwargs["compilation"].executable_sql,
            sql,
        )
        state = create_query.await_args.args[1]
        self.assertEqual(
            state.optimization_status,
            "degraded_fallback",
        )
        self.assertEqual(state.applied_rewrites, ())
        self.assertEqual(
            [diagnostic.code for diagnostic in state.optimization_diagnostics],
            ["missing_limit", "unsupported_function"],
        )
        self.assertEqual(
            state.optimization_diagnostics[1].documentation_anchor,
            "macro-expansion",
        )

    async def test_public_boundary_records_unchanged_compilation(
        self,
    ) -> None:
        runtime = SimpleNamespace(
            config=SimpleNamespace(
                catalogue_alias="atlas",
                catalogue_schema="main",
            ),
            query_bucket=object(),
            preflight=AsyncMock(),
            prepare=AsyncMock(return_value=object()),
        )
        sql = (
            "SELECT document_id FROM documents "
            "ORDER BY document_id"
        )
        create_query = AsyncMock()
        with patch(
            "repository.catalogue.interactive.create_catalogue_query",
            new=create_query,
        ):
            await prepare_interactive_query(
                runtime,  # type: ignore[arg-type]
                sql,
            )

        state = create_query.await_args.args[1]
        self.assertEqual(state.optimization_status, "unchanged")
        self.assertEqual(state.applied_rewrites, ())
        self.assertEqual(
            [diagnostic.code for diagnostic in state.optimization_diagnostics],
            ["missing_limit"],
        )


if __name__ == "__main__":
    unittest.main()
