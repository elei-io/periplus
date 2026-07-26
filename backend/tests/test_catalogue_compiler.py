from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import UUID

import duckdb

from catalogue.compiler import (
    CatalogueDefinitionPurpose,
    FullMaterializationPurpose,
    InteractiveQueryPurpose,
    KeyedMaterializationPurpose,
    OptimizationCode,
    QueryOptimizationUnavailable,
    ScalarMacroDefinition,
    ScalarFunctionDefinition,
    TableMacroDefinition,
    ViewDefinition,
    SqlCompilationOutcome,
    compile_catalogue_query,
    compile_catalogue_sql,
    compile_interactive_catalogue_query,
)

_CHANGED_KEYS = "_atlas_materialization_changed_keys"


def _compile(
    sql: str,
    *,
    source_table: str = "documents",
    key_columns: tuple[str, ...] = ("document_id",),
    key_rows: tuple[tuple[object, ...], ...] | None = None,
    scalar_macros: tuple[ScalarMacroDefinition, ...] = (),
    table_macros: tuple[TableMacroDefinition, ...] = (),
) -> str:
    return compile_catalogue_query(
        sql,
        purpose=KeyedMaterializationPurpose(
            source_table=source_table,
            key_columns=key_columns,
            changed_keys_relation=_CHANGED_KEYS,
            key_rows=key_rows,
            scalar_macros=scalar_macros,
            table_macros=table_macros,
        ),
    )


class CatalogueCompilerTests(unittest.TestCase):
    def test_views_expand_recursively_from_authoritative_definitions(
        self,
    ) -> None:
        result = compile_catalogue_sql(
            "SELECT document_id FROM views.filtered LIMIT 5",
            purpose=InteractiveQueryPurpose(
                views=(
                    ViewDefinition(
                        schema_name="views",
                        view_name="base",
                        sql="SELECT document_id, active FROM documents",
                    ),
                    ViewDefinition(
                        schema_name="views",
                        view_name="filtered",
                        sql=(
                            "SELECT document_id FROM views.base "
                            "WHERE active"
                        ),
                    ),
                )
            ),
        )

        self.assertTrue(result.supported)
        self.assertNotIn("views.filtered", result.executable_sql or "")
        self.assertNotIn("views.base", result.executable_sql or "")

    def test_scalar_macro_evaluation_is_bounded_after_order_and_offset(
        self,
    ) -> None:
        definition = ScalarMacroDefinition(
            schema_name="macros",
            macro_name="expensive_macro",
            parameters=("requested_key",),
            sql=(
                "(SELECT sum(value) FROM facts "
                "WHERE facts.key = requested_key)"
            ),
        )
        sql = (
            "SELECT key, macros.expensive_macro(key) AS value "
            "FROM source ORDER BY key LIMIT 3 OFFSET 2"
        )

        result = compile_catalogue_sql(
            sql,
            purpose=InteractiveQueryPurpose(
                scalar_macros=(definition,)
            ),
        )

        self.assertTrue(result.supported)
        self.assertIn("AS MATERIALIZED", result.executable_sql or "")
        self.assertEqual(
            [rewrite.rule for rewrite in result.applied_rewrites][0],
            "bounded_scalar_input",
        )
        self.assertNotIn(
            "unbounded_dom_helper",
            [diagnostic.code for diagnostic in result.diagnostics],
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE source(key INTEGER); "
            "CREATE TABLE facts(key INTEGER, value INTEGER); "
            "INSERT INTO source VALUES (5), (1), (4), (2), (3), (6); "
            "INSERT INTO facts VALUES "
            "(1, 10), (2, 20), (3, 30), (4, 40), (5, 50), (6, 60); "
            "CREATE SCHEMA macros; "
            "CREATE MACRO macros.expensive_macro(requested_key) AS "
            "(SELECT sum(value) FROM facts "
            " WHERE facts.key = requested_key)"
        )
        self.assertEqual(
            connection.execute(result.executable_sql).fetchall(),
            connection.execute(sql).fetchall(),
        )

    def test_duckdb_stored_builtin_does_not_block_bounded_macro_input(
        self,
    ) -> None:
        definition = ScalarMacroDefinition(
            schema_name="macros",
            macro_name="readable_text",
            parameters=("requested_document_id", "requested_element_index"),
            sql=(
                "(SELECT main.\"trim\"(coalesce(string_agg("
                "child.fragment, '' ORDER BY child.element_index), '')) "
                "FROM elements AS child "
                "WHERE child.document_id = requested_document_id "
                "AND child.element_index >= requested_element_index)"
            ),
        )
        sql = (
            "SELECT macros.readable_text(document_id, element_index) "
            "FROM elements LIMIT 10"
        )

        result = compile_catalogue_sql(
            sql,
            purpose=InteractiveQueryPurpose(
                scalar_macros=(definition,)
            ),
        )

        self.assertTrue(result.supported)
        self.assertIn("AS MATERIALIZED", result.executable_sql or "")
        self.assertIn(
            "bounded_scalar_input",
            [rewrite.rule for rewrite in result.applied_rewrites],
        )
        self.assertNotIn(
            "unbounded_dom_helper",
            [diagnostic.code for diagnostic in result.diagnostics],
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE elements("
            "document_id INTEGER, element_index INTEGER, fragment VARCHAR"
            "); "
            "INSERT INTO elements "
            "SELECT value // 4, value, ' text ' "
            "FROM range(20) AS values(value); "
            "CREATE SCHEMA macros; "
            "CREATE MACRO macros.readable_text("
            "requested_document_id, requested_element_index"
            ") AS ("
            "SELECT main.\"trim\"(coalesce(string_agg("
            "child.fragment, '' ORDER BY child.element_index), '')) "
            "FROM elements AS child "
            "WHERE child.document_id = requested_document_id "
            "AND child.element_index >= requested_element_index"
            ")"
        )
        self.assertEqual(
            connection.execute(result.executable_sql).fetchall(),
            connection.execute(sql).fetchall(),
        )

    def test_same_named_non_main_function_is_not_treated_as_builtin(
        self,
    ) -> None:
        result = compile_catalogue_sql(
            "SELECT macros.effect(key) FROM source LIMIT 3",
            purpose=InteractiveQueryPurpose(
                scalar_macros=(
                    ScalarMacroDefinition(
                        schema_name="macros",
                        macro_name="effect",
                        parameters=("key",),
                        sql='other_schema."trim"(key)',
                    ),
                )
            ),
        )

        self.assertNotIn(
            "bounded_scalar_input",
            [rewrite.rule for rewrite in result.applied_rewrites],
        )

    def test_volatile_scalar_macro_is_not_moved_across_limit(self) -> None:
        result = compile_catalogue_sql(
            "SELECT macros.unstable(key) FROM source LIMIT 3",
            purpose=InteractiveQueryPurpose(
                scalar_macros=(
                    ScalarMacroDefinition(
                        schema_name="macros",
                        macro_name="unstable",
                        parameters=("key",),
                        sql="key + random()",
                    ),
                )
            ),
        )

        self.assertNotIn(
            "bounded_scalar_input",
            [rewrite.rule for rewrite in result.applied_rewrites],
        )

    def test_side_effecting_function_inside_macro_is_not_moved(self) -> None:
        result = compile_catalogue_sql(
            "SELECT macros.effect(key) FROM source LIMIT 3",
            purpose=InteractiveQueryPurpose(
                scalar_macros=(
                    ScalarMacroDefinition(
                        schema_name="macros",
                        macro_name="effect",
                        parameters=("key",),
                        sql="side_effect_udf(key)",
                    ),
                ),
                scalar_functions=(
                    ScalarFunctionDefinition(
                        schema_name="main",
                        function_name="side_effect_udf",
                        has_side_effects=True,
                        stability="VOLATILE",
                    ),
                ),
            ),
        )

        self.assertNotIn(
            "bounded_scalar_input",
            [rewrite.rule for rewrite in result.applied_rewrites],
        )

    def test_same_named_safe_metadata_cannot_approve_anonymous_udf(self) -> None:
        result = compile_catalogue_sql(
            "SELECT macros.effect(key) FROM source ORDER BY key LIMIT 3",
            purpose=InteractiveQueryPurpose(
                scalar_macros=(
                    ScalarMacroDefinition(
                        schema_name="macros",
                        macro_name="effect",
                        parameters=("key",),
                        sql="effect_udf(key)",
                    ),
                ),
                scalar_functions=(
                    ScalarFunctionDefinition(
                        schema_name="other_schema",
                        function_name="effect_udf",
                        has_side_effects=False,
                        stability="CONSISTENT",
                    ),
                ),
            ),
        )

        self.assertNotIn(
            "bounded_scalar_input",
            [rewrite.rule for rewrite in result.applied_rewrites],
        )

    def test_central_sql_chokepoint_is_non_throwing_for_expected_outcomes(
        self,
    ) -> None:
        invalid = compile_catalogue_sql(
            "SELECT FROM",
            purpose=InteractiveQueryPurpose(),
        )
        self.assertEqual(invalid.outcome, SqlCompilationOutcome.INVALID)
        self.assertIsNone(invalid.executable_sql)
        self.assertFalse(invalid.valid)
        self.assertEqual(invalid.diagnostics[0].severity, "error")

        unsupported_sql = (
            "SELECT macros.installed(title) AS title FROM documents"
        )
        unsupported = compile_catalogue_sql(
            unsupported_sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertEqual(
            unsupported.outcome,
            SqlCompilationOutcome.UNSUPPORTED,
        )
        self.assertEqual(unsupported.executable_sql, unsupported_sql)
        self.assertTrue(unsupported.valid)
        self.assertFalse(unsupported.supported)
        self.assertEqual(
            unsupported.diagnostics[-1].code,
            OptimizationCode.UNSUPPORTED_FUNCTION.value,
        )

    def test_central_sql_chokepoint_reports_rewrites_and_advisories(
        self,
    ) -> None:
        sql = (
            "SELECT document_id, upper(title) AS first_title, "
            "  upper(title) AS second_title "
            "FROM documents "
            "ORDER BY document_id, first_title, second_title"
        )
        result = compile_catalogue_sql(
            sql,
            purpose=InteractiveQueryPurpose(),
        )

        self.assertEqual(result.outcome, SqlCompilationOutcome.OPTIMIZED)
        self.assertTrue(result.valid)
        self.assertTrue(result.supported)
        self.assertIn("CROSS JOIN LATERAL", result.executable_sql or "")
        self.assertEqual(
            [rewrite.rule for rewrite in result.applied_rewrites],
            ["repeated_scalar_expression"],
        )
        self.assertEqual(
            [diagnostic.code for diagnostic in result.diagnostics],
            ["missing_limit"],
        )

    def test_central_sql_chokepoint_distinguishes_unchanged_and_eligibility(
        self,
    ) -> None:
        unchanged = compile_catalogue_sql(
            "SELECT document_id FROM documents ORDER BY document_id",
            purpose=FullMaterializationPurpose(),
        )
        self.assertEqual(
            unchanged.outcome,
            SqlCompilationOutcome.UNCHANGED,
        )
        self.assertTrue(unchanged.materialization_eligible)

        incompatible = compile_catalogue_sql(
            "SELECT document_id FROM documents LIMIT 10",
            purpose=KeyedMaterializationPurpose(
                source_table="documents",
                key_columns=("document_id",),
            ),
        )
        self.assertEqual(
            incompatible.outcome,
            SqlCompilationOutcome.UNSUPPORTED,
        )
        self.assertFalse(incompatible.materialization_eligible)
        self.assertIsNone(incompatible.executable_sql)

    def test_duckdb_sampling_is_preserved_or_fails_materialization_closed(
        self,
    ) -> None:
        sql = "SELECT id FROM t USING SAMPLE 100 PERCENT ORDER BY id"

        interactive = compile_catalogue_sql(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        full = compile_catalogue_sql(
            sql,
            purpose=FullMaterializationPurpose(),
        )
        keyed = compile_catalogue_sql(
            sql,
            purpose=KeyedMaterializationPurpose(
                source_table="t",
                key_columns=("id",),
            ),
        )

        self.assertEqual(interactive.outcome, SqlCompilationOutcome.UNCHANGED)
        self.assertEqual(interactive.executable_sql, sql)
        with duckdb.connect() as connection:
            connection.execute("CREATE TABLE t(id INTEGER)")
            connection.execute("INSERT INTO t VALUES (1), (2)")
            self.assertEqual(
                connection.execute(interactive.executable_sql).fetchall(),
                [(1,), (2,)],
            )
        for result in (full, keyed):
            self.assertEqual(result.outcome, SqlCompilationOutcome.UNSUPPORTED)
            self.assertFalse(result.materialization_eligible)
            self.assertIsNone(result.executable_sql)

    def test_central_sql_chokepoint_logs_coverage_without_raw_sql(
        self,
    ) -> None:
        with self.assertLogs(
            "catalogue.compiler.result",
            level="INFO",
        ) as captured:
            compile_catalogue_sql(
                "SELECT 'private value' AS secret",
                purpose=InteractiveQueryPurpose(),
                coverage_source="interactive_execution",
            )

        message = captured.output[0]
        self.assertIn("source=interactive_execution", message)
        self.assertIn("purpose=interactive", message)
        self.assertIn("outcome=unchanged", message)
        self.assertIn("category=none", message)
        self.assertIn("compiler_version=internal", message)
        self.assertIn("catalogue_revision=-", message)
        self.assertIn("latency_seconds=", message)
        self.assertIn("fingerprint=", message)
        self.assertNotIn("private value", message)

    def test_every_compilation_emits_bounded_central_metrics(self) -> None:
        with patch(
            "observability.compiler_metrics.completed",
        ) as completed:
            compile_catalogue_sql(
                "SELECT 1",
                purpose=InteractiveQueryPurpose(),
                coverage_source="caller-controlled-value",
            )

        completed.assert_called_once()
        self.assertEqual(completed.call_args.kwargs["source"], "other")
        self.assertEqual(
            completed.call_args.kwargs["diagnostic_category"],
            "none",
        )

    def test_definition_authoring_has_a_distinct_coverage_source(self) -> None:
        with patch(
            "observability.compiler_metrics.completed",
        ) as completed:
            compile_catalogue_sql(
                "SELECT 1",
                purpose=CatalogueDefinitionPurpose(
                    kind="view",
                    schema_name="views",
                    object_name="example",
                ),
                coverage_source="definition_authoring",
            )

        self.assertEqual(
            completed.call_args.kwargs["source"],
            "definition_authoring",
        )

    def test_full_materialization_accepts_broad_read_only_sql(self) -> None:
        purpose = FullMaterializationPurpose(
            scalar_macros=(
                ScalarMacroDefinition(
                    schema_name="macros",
                    macro_name="normalized",
                    parameters=("value",),
                    sql="lower(trim(value))",
                ),
            ),
        )
        sql = (
            "WITH ranked AS ("
            "  SELECT document_id, title, "
            "  row_number() OVER (ORDER BY document_id DESC) AS position "
            "  FROM documents"
            ") "
            "SELECT document_id, macros.normalized(title) AS title "
            "FROM ranked WHERE position <= 3 "
            "ORDER BY document_id DESC LIMIT 2"
        )
        compiled = compile_catalogue_query(sql, purpose=purpose)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE SCHEMA macros; "
            "CREATE MACRO macros.normalized(value) AS lower(trim(value)); "
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR); "
            "INSERT INTO documents VALUES "
            "(1, ' One '), (2, ' Two '), (3, ' Three '), (4, ' Four ')"
        )
        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertNotIn("macros.normalized", compiled)
        self.assertIn("ROW_NUMBER()", compiled)
        self.assertIn("LIMIT 2", compiled)

    def test_keyed_materialization_accepts_deterministic_regex_functions(
        self,
    ) -> None:
        sql = (
            "WITH normalized AS ("
            "  SELECT document_id, "
            "    regexp_replace(title, '[[:space:]]+', ' ', 'g') AS title "
            "  FROM documents"
            ") "
            "SELECT document_id, title FROM normalized"
        )

        compiled = _compile(sql)

        self.assertIn("REGEXP_REPLACE", compiled)
        self.assertIn(_CHANGED_KEYS, compiled)

    def test_keyed_materialization_accepts_seeded_passage_text_expression(
        self,
    ) -> None:
        sql = """
            SELECT
                document_id,
                trim(
                    regexp_replace(
                        coalesce(
                            listagg(
                                fragment,
                                ''
                                ORDER BY
                                    event_index,
                                    event_phase,
                                    depth DESC,
                                    depth_phase,
                                    fragment
                            ),
                            ''
                        ),
                        '\\s+',
                        ' ',
                        'g'
                    )
                ) AS passage_text
            FROM document_fragments
            GROUP BY document_id
        """

        compiled = _compile(sql, source_table="document_fragments")

        self.assertIn("TRIM", compiled)
        self.assertIn("REGEXP_REPLACE", compiled)
        self.assertIn("LISTAGG", compiled)
        self.assertIn(_CHANGED_KEYS, compiled)

    def test_full_materialization_unresolved_macro_is_structured(self) -> None:
        with self.assertRaises(QueryOptimizationUnavailable) as raised:
            compile_catalogue_query(
                "SELECT macros.missing(title) AS title FROM documents",
                purpose=FullMaterializationPurpose(),
            )
        self.assertEqual(
            raised.exception.code,
            OptimizationCode.UNSUPPORTED_FUNCTION,
        )
        self.assertEqual(
            raised.exception.diagnostic.documentation_anchor,
            "macro-expansion",
        )

    def test_interactive_compilation_resolves_macros_without_key_policy(
        self,
    ) -> None:
        purpose = InteractiveQueryPurpose(
            scalar_macros=(
                ScalarMacroDefinition(
                    schema_name="macros",
                    macro_name="normalized",
                    parameters=("value",),
                    sql="lower(trim(value))",
                ),
            ),
        )
        sql = (
            "SELECT *, macros.normalized(title) AS normalized_title "
            "FROM documents"
        )
        compiled = compile_catalogue_query(sql, purpose=purpose)

        self.assertIn("SELECT", compiled)
        self.assertIn("*", compiled)
        self.assertIn("LOWER(TRIM(title))", compiled)
        self.assertNotIn("macros.normalized", compiled)

    def test_interactive_compilation_preserves_valid_unoptimized_sql(
        self,
    ) -> None:
        sql = "SELECT *, mystery(title) AS value FROM documents"
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertIn("MYSTERY(title)", compiled)

    def test_interactive_explanation_reports_only_applied_rules(
        self,
    ) -> None:
        sql = (
            "WITH scoped AS ("
            "  SELECT document_id, title FROM documents"
            ") "
            "SELECT scoped.document_id FROM scoped "
            "WHERE scoped.document_id > 1 "
            "ORDER BY scoped.document_id"
        )
        purpose = InteractiveQueryPurpose()
        explained = compile_interactive_catalogue_query(
            sql,
            purpose=purpose,
        )

        self.assertEqual(
            [rewrite.rule.value for rewrite in explained.applied_rewrites],
            [
                "predicate_pushdown",
                "projection_pruning",
                "cte_materialization",
            ],
        )
        self.assertTrue(
            all(rewrite.evidence for rewrite in explained.applied_rewrites)
        )
        self.assertEqual(
            compile_catalogue_query(sql, purpose=purpose),
            explained.sql,
        )

    def test_interactive_unresolved_macro_uses_structured_fallback(self) -> None:
        with self.assertRaises(QueryOptimizationUnavailable) as raised:
            compile_catalogue_query(
                "SELECT macros.missing(title) AS value FROM documents",
                purpose=InteractiveQueryPurpose(),
            )
        self.assertEqual(
            raised.exception.code,
            OptimizationCode.UNSUPPORTED_FUNCTION,
        )

    def test_interactive_pushes_predicate_through_derived_projection(
        self,
    ) -> None:
        sql = (
            "SELECT scoped.doc_id, scoped.title "
            "FROM ("
            "  SELECT document_id AS doc_id, title FROM documents "
            "  WHERE title IS NOT NULL"
            ") AS scoped "
            "WHERE scoped.doc_id = 2"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES (1, 'one'), (2, 'two')"
        )
        self.assertCountEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertGreaterEqual(compiled.count("document_id = 2"), 1)
        self.assertIn("scoped.doc_id = 2", compiled)

    def test_interactive_pushes_predicate_into_single_use_cte(self) -> None:
        sql = (
            "WITH selected AS ("
            "  SELECT document_id, title FROM documents"
            ") "
            "SELECT document_id, title FROM selected "
            "WHERE document_id >= 10"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertGreaterEqual(compiled.count("document_id >= 10"), 2)

    def test_interactive_does_not_push_into_unsafe_or_shared_scope(self) -> None:
        cases = (
            (
                "SELECT scoped.document_id "
                "FROM ("
                "  SELECT document_id FROM documents LIMIT 10"
                ") AS scoped "
                "WHERE scoped.document_id = 2",
                1,
            ),
            (
                "WITH selected AS ("
                "  SELECT document_id FROM documents"
                ") "
                "SELECT left_side.document_id "
                "FROM selected AS left_side "
                "JOIN selected AS right_side USING (document_id) "
                "WHERE left_side.document_id = 2",
                2,
            ),
            (
                "SELECT scoped.shifted "
                "FROM ("
                "  SELECT document_id + 1 AS shifted FROM documents"
                ") AS scoped "
                "WHERE scoped.shifted = 2",
                1,
            ),
            (
                "SELECT scoped.document_id, scoped.sample "
                "FROM ("
                "  SELECT document_id, random() AS sample FROM documents"
                ") AS scoped "
                "WHERE scoped.document_id = 2",
                1,
            ),
        )
        for sql, expected_predicates in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertEqual(
                    compiled.count("= 2"),
                    expected_predicates,
                )

    def test_interactive_propagates_predicate_across_inner_join(self) -> None:
        sql = (
            "SELECT document.document_id, element.value "
            "FROM documents AS document "
            "JOIN elements AS element "
            "ON document.document_id = element.owner_document "
            "WHERE document.document_id = 2"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE documents(document_id INTEGER)")
        connection.execute(
            "CREATE TABLE elements(owner_document INTEGER, value VARCHAR)"
        )
        connection.execute("INSERT INTO documents VALUES (1), (2)")
        connection.execute(
            "INSERT INTO elements VALUES (1, 'one'), (2, 'two')"
        )
        self.assertCountEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertIn("element.owner_document = 2", compiled)

    def test_interactive_propagates_using_predicate_into_derived_scan(
        self,
    ) -> None:
        sql = (
            "SELECT document.document_id, element.value "
            "FROM documents AS document "
            "JOIN ("
            "  SELECT document_id, value FROM elements"
            ") AS element USING (document_id) "
            "WHERE document.document_id >= 10"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertGreaterEqual(compiled.count("document_id >= 10"), 2)

    def test_interactive_filters_physical_right_side_of_left_using_join(
        self,
    ) -> None:
        sql = (
            "SELECT document.document_id, element.value "
            "FROM documents AS document "
            "LEFT JOIN elements AS element USING (document_id) "
            "WHERE document.document_id = 2"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE documents(document_id INTEGER)")
        connection.execute(
            "CREATE TABLE elements(document_id INTEGER, value VARCHAR)"
        )
        connection.execute("INSERT INTO documents VALUES (1), (2)")
        connection.execute("INSERT INTO elements VALUES (1, 'one')")
        self.assertCountEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertIn("_atlas_interactive_right", compiled)

    def test_interactive_join_predicate_propagation_is_transitive(self) -> None:
        sql = (
            "SELECT document.document_id "
            "FROM documents AS document "
            "JOIN elements AS element "
            "ON document.document_id = element.owner_document "
            "JOIN attributes AS attribute "
            "ON element.owner_document = attribute.document_ref "
            "WHERE document.document_id = 2"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertIn("element.owner_document = 2", compiled)
        self.assertIn("attribute.document_ref = 2", compiled)

    def test_interactive_does_not_propagate_across_unsafe_join(self) -> None:
        cases = (
            (
                "SELECT document.document_id "
                "FROM documents AS document "
                "LEFT JOIN elements AS element "
                "ON document.document_id = element.document_id "
                "WHERE document.document_id = 2",
                2,
            ),
            (
                "SELECT document.document_id "
                "FROM documents AS document "
                "JOIN elements AS element "
                "ON document.category = element.category "
                "WHERE document.document_id = 2",
                1,
            ),
            (
                "SELECT document.document_id "
                "FROM documents AS document "
                "CROSS JOIN elements AS element "
                "WHERE document.document_id = 2",
                1,
            ),
        )
        for sql, expected_predicates in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertEqual(
                    compiled.count("document_id = 2"),
                    expected_predicates,
                )

    def test_interactive_constrains_left_join_without_losing_unmatched_rows(
        self,
    ) -> None:
        sql = (
            "SELECT document.document_id, element.value "
            "FROM documents AS document "
            "LEFT JOIN elements AS element "
            "ON document.document_id = element.owner_document "
            "WHERE document.document_id = 2"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE documents(document_id INTEGER)")
        connection.execute(
            "CREATE TABLE elements(owner_document INTEGER, value VARCHAR)"
        )
        connection.execute("INSERT INTO documents VALUES (1), (2)")
        connection.execute("INSERT INTO elements VALUES (1, 'one')")
        self.assertCountEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertIn("element.owner_document = 2", compiled)
        self.assertEqual(
            connection.execute(compiled).fetchall(),
            [(2, None)],
        )

    def test_interactive_pushes_left_join_constraint_into_right_scope(
        self,
    ) -> None:
        sql = (
            "SELECT document.document_id, element.value "
            "FROM documents AS document "
            "LEFT JOIN ("
            "  SELECT document_id, value FROM elements"
            ") AS element USING (document_id) "
            "WHERE document.document_id >= 10"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertGreaterEqual(compiled.count("document_id >= 10"), 2)

    def test_interactive_does_not_propagate_from_nullable_left_join_side(
        self,
    ) -> None:
        cases = (
            (
                "SELECT document.document_id "
                "FROM documents AS document "
                "LEFT JOIN elements AS element "
                "ON document.document_id = element.document_id "
                "WHERE element.document_id IS NULL"
            ),
            (
                "SELECT document.document_id "
                "FROM documents AS document "
                "LEFT JOIN elements AS element "
                "ON document.document_id = element.document_id "
                "WHERE element.document_id = 2"
            ),
        )
        for sql in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertEqual(compiled.count("element.document_id"), 2)

    def test_interactive_pushes_filter_into_union_all_branches(self) -> None:
        sql = (
            "SELECT combined.document_id, combined.title "
            "FROM ("
            "  SELECT document_id, title FROM current_documents "
            "  UNION ALL "
            "  SELECT archived_id AS document_id, title "
            "  FROM archived_documents"
            ") AS combined "
            "WHERE combined.document_id >= 2"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE current_documents("
            "document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE archived_documents("
            "archived_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "INSERT INTO current_documents VALUES (1, 'one'), (2, 'two')"
        )
        connection.execute(
            "INSERT INTO archived_documents VALUES "
            "(2, 'old two'), (3, 'three')"
        )

        self.assertCountEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertIn("document_id >= 2", compiled)
        self.assertIn("archived_id >= 2", compiled)
        self.assertIn("combined.document_id >= 2", compiled)

    def test_interactive_pushes_union_filter_through_explicit_output_names(
        self,
    ) -> None:
        sql = (
            "SELECT combined.document_id "
            "FROM ("
            "  SELECT current_id FROM current_documents "
            "  WHERE current_id IS NOT NULL "
            "  UNION ALL "
            "  SELECT archived_id FROM archived_documents"
            ") AS combined(document_id) "
            "WHERE combined.document_id = 2"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE current_documents(current_id INTEGER)"
        )
        connection.execute(
            "CREATE TABLE archived_documents(archived_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO current_documents VALUES (NULL), (2)"
        )
        connection.execute(
            "INSERT INTO archived_documents VALUES (1), (2)"
        )

        self.assertCountEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertIn("NOT current_id IS NULL", compiled)
        self.assertIn("current_id = 2", compiled)
        self.assertIn("archived_id = 2", compiled)

    def test_interactive_union_filter_pushdown_respects_barriers(self) -> None:
        cases = (
            (
                "SELECT combined.document_id "
                "FROM ("
                "  SELECT document_id FROM current_documents "
                "  UNION "
                "  SELECT document_id FROM archived_documents"
                ") AS combined "
                "WHERE combined.document_id = 2",
                1,
            ),
            (
                "SELECT combined.document_id "
                "FROM ("
                "  SELECT document_id FROM current_documents "
                "  UNION ALL BY NAME "
                "  SELECT document_id FROM archived_documents"
                ") AS combined "
                "WHERE combined.document_id = 2",
                1,
            ),
            (
                "SELECT combined.document_id "
                "FROM ("
                "  SELECT document_id FROM current_documents "
                "  UNION ALL "
                "  SELECT document_id + 1 AS document_id "
                "  FROM archived_documents"
                ") AS combined "
                "WHERE combined.document_id = 2",
                1,
            ),
        )
        for sql, expected_predicates in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertEqual(
                    compiled.count("= 2"),
                    expected_predicates,
                )

    def test_interactive_pushes_group_key_filter_before_aggregation(
        self,
    ) -> None:
        sql = (
            "SELECT grouped.kind, grouped.total "
            "FROM ("
            "  SELECT category AS kind, COUNT(*) AS total "
            "  FROM documents "
            "  WHERE visible "
            "  GROUP BY category "
            "  HAVING COUNT(*) >= 1"
            ") AS grouped "
            "WHERE grouped.kind IN ('news', 'guide')"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(category VARCHAR, visible BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "('news', true), ('news', false), "
            "('guide', true), ('other', true), (NULL, true)"
        )

        self.assertCountEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertEqual(compiled.count("IN ('news', 'guide')"), 2)
        self.assertIn(
            "visible AND category IN ('news', 'guide')",
            compiled,
        )

    def test_interactive_aggregate_input_filter_respects_barriers(self) -> None:
        cases = (
            (
                "SELECT grouped.total "
                "FROM ("
                "  SELECT category, COUNT(*) AS total "
                "  FROM documents GROUP BY category"
                ") AS grouped "
                "WHERE grouped.total >= 2",
                ">= 2",
                1,
            ),
            (
                "SELECT grouped.kind "
                "FROM ("
                "  SELECT upper(category) AS kind, COUNT(*) AS total "
                "  FROM documents GROUP BY upper(category)"
                ") AS grouped "
                "WHERE grouped.kind = 'NEWS'",
                "= 'NEWS'",
                1,
            ),
            (
                "SELECT grouped.category "
                "FROM ("
                "  SELECT category, COUNT(*) AS total "
                "  FROM documents GROUP BY ROLLUP(category)"
                ") AS grouped "
                "WHERE grouped.category = 'news'",
                "= 'news'",
                1,
            ),
        )
        for sql, predicate_sql, expected_predicates in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertEqual(
                    compiled.count(predicate_sql),
                    expected_predicates,
                )

    def test_interactive_recognizes_grouping_aliases_and_ordinals(self) -> None:
        groupings = ("kind", "1")
        for grouping in groupings:
            sql = (
                "SELECT grouped.kind, grouped.total "
                "FROM ("
                "  SELECT category AS kind, COUNT(*) AS total "
                "  FROM documents "
                f"  GROUP BY {grouping}"
                ") AS grouped "
                "WHERE grouped.kind = 'news'"
            )
            with self.subTest(grouping=grouping):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                connection = duckdb.connect()
                self.addCleanup(connection.close)
                connection.execute(
                    "CREATE TABLE documents(category VARCHAR)"
                )
                connection.execute(
                    "INSERT INTO documents VALUES "
                    "('news'), ('news'), ('other')"
                )
                self.assertCountEqual(
                    connection.execute(compiled).fetchall(),
                    connection.execute(sql).fetchall(),
                )
                self.assertIn("category = 'news'", compiled)

    def test_interactive_pushes_group_key_having_conjunct_to_input(
        self,
    ) -> None:
        sql = (
            "SELECT category AS kind, COUNT(*) AS total "
            "FROM documents "
            "WHERE visible "
            "GROUP BY category "
            "HAVING kind IN ('news', 'guide') AND COUNT(*) >= 2"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(category VARCHAR, visible BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "('news', true), ('news', true), ('news', false), "
            "('guide', true), ('other', true), ('other', true)"
        )

        self.assertCountEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertIn(
            "visible AND category IN ('news', 'guide')",
            compiled,
        )
        self.assertIn(
            "HAVING\n  kind IN ('news', 'guide') AND COUNT(*) >= 2",
            compiled,
        )

    def test_interactive_having_pushdown_respects_barriers(self) -> None:
        cases = (
            (
                "SELECT category, COUNT(*) AS total "
                "FROM documents GROUP BY category "
                "HAVING COUNT(*) >= 2",
                "COUNT(*) >= 2",
            ),
            (
                "SELECT category, COUNT(*) AS total "
                "FROM documents GROUP BY category "
                "HAVING category = 'news' OR COUNT(*) >= 2",
                "category = 'news'",
            ),
            (
                "SELECT upper(category) AS kind, COUNT(*) AS total "
                "FROM documents GROUP BY upper(category) "
                "HAVING kind = 'NEWS'",
                "kind = 'NEWS'",
            ),
            (
                "SELECT category, COUNT(*) AS total "
                "FROM documents GROUP BY ROLLUP(category) "
                "HAVING category = 'news'",
                "category = 'news'",
            ),
        )
        for sql, predicate_sql in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertEqual(compiled.count(predicate_sql), 1)

    def test_interactive_having_pushdown_preserves_null_group(self) -> None:
        sql = (
            "SELECT category AS kind, COUNT(*) AS total "
            "FROM documents "
            "GROUP BY category "
            "HAVING category IS NULL"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE documents(category VARCHAR)")
        connection.execute(
            "INSERT INTO documents VALUES (NULL), (NULL), ('news')"
        )

        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertEqual(compiled.count("category IS NULL"), 2)

    def test_interactive_pushes_whole_window_partition_filter(
        self,
    ) -> None:
        sql = (
            "SELECT ranked.category, ranked.item_id, ranked.position "
            "FROM ("
            "  SELECT category, item_id, "
            "    ROW_NUMBER() OVER ("
            "      PARTITION BY category ORDER BY score DESC, item_id"
            "    ) AS position "
            "  FROM documents "
            "  WHERE visible "
            "  QUALIFY position <= 2"
            ") AS ranked "
            "WHERE ranked.category = 'news'"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "category VARCHAR, item_id INTEGER, score INTEGER, "
            "visible BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "('news', 1, 10, true), ('news', 2, 20, true), "
            "('other', 3, 100, true), ('news', 4, 30, false)"
        )

        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertEqual(compiled.count("category = 'news'"), 2)
        self.assertIn(
            "visible AND category = 'news'",
            compiled,
        )

    def test_interactive_filters_key_common_to_every_window_partition(
        self,
    ) -> None:
        sql = (
            "SELECT ranked.category, ranked.item_id "
            "FROM ("
            "  SELECT category, region, item_id, "
            "    ROW_NUMBER() OVER ("
            "      PARTITION BY category ORDER BY score"
            "    ) AS category_position, "
            "    COUNT(*) OVER ("
            "      PARTITION BY category, region"
            "    ) AS regional_count "
            "  FROM documents"
            ") AS ranked "
            "WHERE ranked.category = 'news'"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertEqual(compiled.count("category = 'news'"), 2)

    def test_interactive_window_filter_pushdown_respects_barriers(self) -> None:
        cases = (
            (
                "SELECT ranked.item_id "
                "FROM ("
                "  SELECT category, item_id, "
                "    ROW_NUMBER() OVER (PARTITION BY category "
                "      ORDER BY score) AS position "
                "  FROM documents"
                ") AS ranked "
                "WHERE ranked.item_id = 2",
                "item_id = 2",
            ),
            (
                "SELECT ranked.category "
                "FROM ("
                "  SELECT category, "
                "    ROW_NUMBER() OVER (ORDER BY score) AS position "
                "  FROM documents"
                ") AS ranked "
                "WHERE ranked.category = 'news'",
                "category = 'news'",
            ),
            (
                "SELECT ranked.category "
                "FROM ("
                "  SELECT category, "
                "    ROW_NUMBER() OVER (PARTITION BY category "
                "      ORDER BY score) AS category_position, "
                "    ROW_NUMBER() OVER (PARTITION BY region "
                "      ORDER BY score) AS region_position "
                "  FROM documents"
                ") AS ranked "
                "WHERE ranked.category = 'news'",
                "category = 'news'",
            ),
            (
                "SELECT ranked.category "
                "FROM ("
                "  SELECT category, "
                "    ROW_NUMBER() OVER ranking AS position "
                "  FROM documents "
                "  WINDOW ranking AS (PARTITION BY category "
                "    ORDER BY score)"
                ") AS ranked "
                "WHERE ranked.category = 'news'",
                "category = 'news'",
            ),
        )
        for sql, predicate_sql in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertEqual(compiled.count(predicate_sql), 1)

    def test_interactive_prunes_unused_cte_projection_columns(self) -> None:
        sql = (
            "WITH selected AS ("
            "  SELECT document_id, title, body, unused "
            "  FROM documents"
            ") "
            "SELECT selected.document_id "
            "FROM selected "
            "WHERE selected.title = 'kept'"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, body VARCHAR, "
            "unused VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'kept', 'body', 'unused'), "
            "(2, 'removed', 'body', 'unused')"
        )

        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertIn("document_id,\n    title", compiled)
        self.assertNotIn("body", compiled)
        self.assertNotIn("unused", compiled)

    def test_interactive_projection_pruning_tracks_nested_dependencies(
        self,
    ) -> None:
        sql = (
            "WITH enriched AS ("
            "  SELECT document_id, category, score, payload, unused "
            "  FROM documents"
            "), ranked AS ("
            "  SELECT document_id, category, score, payload, "
            "    ROW_NUMBER() OVER ("
            "      PARTITION BY category ORDER BY score DESC"
            "    ) AS position "
            "  FROM enriched"
            ") "
            "SELECT ranked.document_id "
            "FROM ranked "
            "JOIN categories USING (category) "
            "WHERE ranked.position = 1 "
            "ORDER BY ranked.document_id"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, category VARCHAR, score INTEGER, "
            "payload VARCHAR, unused VARCHAR)"
        )
        connection.execute("CREATE TABLE categories(category VARCHAR)")
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'news', 10, 'one', 'x'), "
            "(2, 'news', 20, 'two', 'x'), "
            "(3, 'guide', 30, 'three', 'x')"
        )
        connection.execute(
            "INSERT INTO categories VALUES ('news'), ('guide')"
        )

        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertNotIn("payload", compiled)
        self.assertNotIn("unused", compiled)
        self.assertIn("score", compiled)
        self.assertIn("category", compiled)

    def test_interactive_projection_pruning_unions_shared_cte_needs(
        self,
    ) -> None:
        sql = (
            "WITH selected AS ("
            "  SELECT document_id, title, body, unused FROM documents"
            ") "
            "SELECT left_side.document_id "
            "FROM selected AS left_side "
            "JOIN selected AS right_side "
            "  ON left_side.document_id = right_side.document_id "
            "WHERE right_side.title = 'kept'"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertIn("document_id,\n    title", compiled)
        self.assertNotIn("body", compiled)
        self.assertNotIn("unused", compiled)

    def test_interactive_projection_pruning_retains_clause_dependencies(
        self,
    ) -> None:
        sql = (
            "WITH source AS ("
            "  SELECT category, score, title, unused FROM documents"
            ") "
            "SELECT category, COUNT(*) "
            "FROM source "
            "WHERE title IS NOT NULL "
            "GROUP BY category "
            "HAVING SUM(score) >= 10 "
            "ORDER BY category"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "category VARCHAR, score INTEGER, title VARCHAR, "
            "unused VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "('news', 10, 'one', 'x'), ('news', 5, NULL, 'x'), "
            "('guide', 20, 'two', 'x')"
        )

        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertIn("category", compiled)
        self.assertIn("score", compiled)
        self.assertIn("title", compiled)
        self.assertNotIn("unused", compiled)

    def test_interactive_prunes_expanded_table_macro_projection(
        self,
    ) -> None:
        definition = TableMacroDefinition(
            schema_name="macros",
            macro_name="document_details",
            parameters=(),
            parameter_defaults=(),
            sql=(
                "SELECT document_id, title, body, unused "
                "FROM documents"
            ),
        )
        compiled = compile_catalogue_query(
            "SELECT detail.document_id "
            "FROM macros.document_details() AS detail",
            purpose=InteractiveQueryPurpose(
                table_macros=(definition,),
            ),
        )
        self.assertIn("document_id", compiled)
        self.assertNotIn("title", compiled)
        self.assertNotIn("body", compiled)
        self.assertNotIn("unused", compiled)

    def test_interactive_projection_pruning_respects_barriers(self) -> None:
        cases = (
            (
                "SELECT scoped.document_id "
                "FROM ("
                "  SELECT DISTINCT document_id, title FROM documents"
                ") AS scoped",
                "title",
            ),
            (
                "SELECT scoped.document_id "
                "FROM (SELECT * FROM documents) AS scoped",
                "*",
            ),
            (
                "SELECT scoped.document_id "
                "FROM ("
                "  SELECT document_id, title, random() AS sample "
                "  FROM documents"
                ") AS scoped",
                "title",
            ),
            (
                "SELECT scoped.document_id "
                "FROM ("
                "  SELECT document_id, title "
                "  FROM documents ORDER BY 2"
                ") AS scoped",
                "title",
            ),
        )
        for sql, retained_sql in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertIn(retained_sql, compiled)

    def test_interactive_eliminates_redundant_derived_projection(
        self,
    ) -> None:
        sql = (
            "SELECT detail.doc_id, detail.heading "
            "FROM ("
            "  SELECT document_id AS doc_id, title AS heading "
            "  FROM documents AS source"
            ") AS detail "
            "WHERE detail.doc_id >= 2 "
            "ORDER BY detail.heading"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'one'), (2, 'two'), (3, 'three')"
        )

        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertNotIn("FROM (\n", compiled)
        self.assertIn("documents AS detail", compiled)
        self.assertIn("detail.document_id >= 2", compiled)
        self.assertIn("detail.title", compiled)

    def test_interactive_eliminates_nested_redundant_derived_scopes(
        self,
    ) -> None:
        sql = (
            "SELECT outer_scope.final_id "
            "FROM ("
            "  SELECT inner_scope.doc_id AS final_id "
            "  FROM ("
            "    SELECT document_id AS doc_id FROM documents"
            "  ) AS inner_scope"
            ") AS outer_scope "
            "WHERE outer_scope.final_id = 2"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertNotIn("FROM (\n", compiled)
        self.assertIn("documents AS outer_scope", compiled)
        self.assertIn("outer_scope.document_id = 2", compiled)

    def test_interactive_eliminates_redundant_nullable_join_scope(
        self,
    ) -> None:
        sql = (
            "SELECT document.document_id, element.document_id, element.value "
            "FROM documents AS document "
            "LEFT JOIN ("
            "  SELECT owner_id AS document_id, value FROM elements"
            ") AS element "
            "ON document.document_id = element.document_id "
            "ORDER BY document.document_id"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE documents(document_id INTEGER)")
        connection.execute(
            "CREATE TABLE elements(owner_id INTEGER, value VARCHAR)"
        )
        connection.execute("INSERT INTO documents VALUES (1), (2)")
        connection.execute("INSERT INTO elements VALUES (1, 'one')")

        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertNotIn("LEFT JOIN (\n", compiled)
        self.assertIn("LEFT JOIN elements AS element", compiled)
        self.assertIn(
            "element.owner_id AS document_id",
            compiled,
        )

    def test_interactive_derived_scope_elimination_respects_barriers(
        self,
    ) -> None:
        cases = (
            (
                "WITH selected AS ("
                "  SELECT document_id FROM documents"
                ") SELECT document_id FROM selected",
                "WITH selected AS",
            ),
            (
                "SELECT scoped.* "
                "FROM ("
                "  SELECT document_id, title FROM documents"
                ") AS scoped",
                "FROM (\n",
            ),
            (
                "SELECT scoped.document_id "
                "FROM ("
                "  SELECT document_id FROM documents WHERE visible"
                ") AS scoped",
                "FROM (\n",
            ),
            (
                "SELECT scoped.shifted "
                "FROM ("
                "  SELECT document_id + 1 AS shifted FROM documents"
                ") AS scoped",
                "FROM (\n",
            ),
            (
                "SELECT scoped.document_id "
                "FROM ("
                "  SELECT document_id FROM documents LIMIT 10"
                ") AS scoped",
                "FROM (\n",
            ),
            (
                "SELECT scoped.document_id "
                "FROM other "
                "JOIN ("
                "  SELECT document_id FROM documents"
                ") AS scoped USING (document_id)",
                "JOIN (\n",
            ),
        )
        for sql, retained_sql in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertIn(retained_sql, compiled)

    def test_interactive_pushes_filter_before_physical_unnest(self) -> None:
        cases = (
            (
                "SELECT document.document_id, "
                "  unnest(document.tags) AS tag "
                "FROM documents AS document "
                "WHERE document.document_id = 2",
                "tag",
            ),
            (
                "SELECT document.document_id, expanded.tag "
                "FROM documents AS document "
                "CROSS JOIN UNNEST(document.tags) AS expanded(tag) "
                "WHERE document.document_id = 2",
                "tag",
            ),
            (
                "SELECT document.document_id, expanded.tag "
                "FROM documents AS document "
                "LEFT JOIN LATERAL "
                "  UNNEST(document.tags) AS expanded(tag) ON TRUE "
                "WHERE document.document_id = 2",
                "tag",
            ),
        )
        for sql, _ in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                connection = duckdb.connect()
                self.addCleanup(connection.close)
                connection.execute(
                    "CREATE TABLE documents("
                    "document_id INTEGER, tags VARCHAR[])"
                )
                connection.execute(
                    "INSERT INTO documents VALUES "
                    "(1, ['other']), (2, ['one', 'two']), (3, [])"
                )
                self.assertCountEqual(
                    connection.execute(compiled).fetchall(),
                    connection.execute(sql).fetchall(),
                )
                self.assertEqual(
                    compiled.count("= 2"),
                    2,
                )
                self.assertIn("_atlas_interactive_input", compiled)

    def test_interactive_pushes_pre_unnest_filter_into_derived_input(
        self,
    ) -> None:
        sql = (
            "SELECT document.document_id, expanded.tag "
            "FROM ("
            "  SELECT document_id, tags FROM documents "
            "  WHERE tags IS NOT NULL"
            ") AS document "
            "CROSS JOIN UNNEST(document.tags) AS expanded(tag) "
            "WHERE document.document_id >= 2"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertEqual(compiled.count("document_id >= 2"), 2)
        self.assertIn(
            "NOT tags IS NULL AND document_id >= 2",
            compiled,
        )

    def test_interactive_unnest_pushdown_respects_dependencies(self) -> None:
        cases = (
            (
                "SELECT document.document_id, expanded.tag "
                "FROM documents AS document "
                "CROSS JOIN UNNEST(document.tags) AS expanded(tag) "
                "WHERE expanded.tag = 'one'",
                "expanded.tag = 'one'",
            ),
            (
                "SELECT document.document_id, expanded.tag "
                "FROM documents AS document "
                "CROSS JOIN UNNEST(document.tags) AS expanded(tag) "
                "WHERE CAST(document.document_id AS VARCHAR) = expanded.tag",
                "= expanded.tag",
            ),
            (
                "SELECT document.document_id, expanded.tag "
                "FROM documents AS document "
                "CROSS JOIN UNNEST(document.tags) AS expanded(tag) "
                "WHERE mystery(document.document_id)",
                "MYSTERY(document.document_id)",
            ),
            (
                "SELECT document.document_id, expanded.tag "
                "FROM documents AS document "
                "CROSS JOIN UNNEST(document.tags) AS expanded(tag) "
                "WHERE document_id = 2",
                "document_id = 2",
            ),
            (
                "SELECT document.document_id, expanded.tag "
                "FROM documents "
                "  AS document(document_id, tags) "
                "CROSS JOIN UNNEST(document.tags) AS expanded(tag) "
                "WHERE document.document_id = 2",
                "document.document_id = 2",
            ),
        )
        for sql, predicate_sql in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertEqual(compiled.count(predicate_sql), 1)
                self.assertNotIn("_atlas_interactive_input", compiled)

    def test_interactive_removes_inner_sort_with_total_outer_order(
        self,
    ) -> None:
        sql = (
            "SELECT scoped.document_id, scoped.title "
            "FROM ("
            "  SELECT document_id, title "
            "  FROM documents "
            "  ORDER BY title DESC"
            ") AS scoped "
            "ORDER BY scoped.document_id, scoped.title"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(2, 'same'), (1, 'same'), (3, NULL)"
        )

        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertEqual(compiled.count("ORDER BY"), 1)
        self.assertNotIn("title DESC", compiled)

    def test_interactive_removes_cte_sort_for_fully_ordered_consumer(
        self,
    ) -> None:
        sql = (
            "WITH selected AS ("
            "  SELECT document_id, title "
            "  FROM documents ORDER BY title"
            ") "
            "SELECT document_id AS id, title "
            "FROM selected "
            "ORDER BY id, title"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertEqual(compiled.count("ORDER BY"), 1)

    def test_interactive_sort_removal_respects_observability(self) -> None:
        cases = (
            (
                "SELECT scoped.document_id, scoped.title "
                "FROM ("
                "  SELECT document_id, title FROM documents "
                "  ORDER BY title"
                ") AS scoped "
                "ORDER BY scoped.document_id",
                2,
            ),
            (
                "SELECT scoped.document_id, scoped.title "
                "FROM ("
                "  SELECT document_id, title FROM documents "
                "  ORDER BY title LIMIT 10"
                ") AS scoped "
                "ORDER BY scoped.document_id, scoped.title",
                2,
            ),
            (
                "SELECT scoped.document_id, scoped.title, "
                "  ROW_NUMBER() OVER () AS position "
                "FROM ("
                "  SELECT document_id, title FROM documents "
                "  ORDER BY title"
                ") AS scoped "
                "ORDER BY scoped.document_id, scoped.title, position",
                2,
            ),
            (
                "SELECT scoped.* "
                "FROM ("
                "  SELECT document_id, title FROM documents "
                "  ORDER BY title"
                ") AS scoped "
                "ORDER BY scoped.document_id, scoped.title",
                2,
            ),
            (
                "SELECT scoped.document_id, scoped.title "
                "FROM ("
                "  SELECT document_id, title FROM documents "
                "  ORDER BY random()"
                ") AS scoped "
                "ORDER BY scoped.document_id, scoped.title",
                2,
            ),
            (
                "WITH selected AS MATERIALIZED ("
                "  SELECT document_id, title "
                "  FROM documents ORDER BY title"
                ") "
                "SELECT document_id, title FROM selected "
                "ORDER BY document_id, title",
                2,
            ),
        )
        for sql, expected_sorts in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertEqual(compiled.count("ORDER BY"), expected_sorts)

    def test_interactive_pushes_ordered_limit_offset_prefix(self) -> None:
        sql = (
            "SELECT scoped.document_id, scoped.title "
            "FROM ("
            "  SELECT document_id, title "
            "  FROM documents "
            "  ORDER BY document_id, title"
            ") AS scoped "
            "LIMIT 2 OFFSET 1"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(3, 'three'), (1, 'one'), (4, 'four'), (2, 'two')"
        )

        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertEqual(compiled.count("LIMIT"), 2)
        self.assertIn("LIMIT 3", compiled)
        self.assertIn("LIMIT 2\nOFFSET 1", compiled)

    def test_interactive_pushes_ordered_limit_into_single_use_cte(
        self,
    ) -> None:
        sql = (
            "WITH selected AS ("
            "  SELECT document_id, title "
            "  FROM documents ORDER BY document_id, title"
            ") "
            "SELECT document_id, title FROM selected LIMIT 5"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertEqual(compiled.count("LIMIT 5"), 2)

    def test_interactive_limit_pushdown_respects_prefix_barriers(self) -> None:
        cases = (
            (
                "SELECT scoped.document_id "
                "FROM ("
                "  SELECT document_id FROM documents"
                ") AS scoped LIMIT 5",
                "LIMIT 5",
            ),
            (
                "SELECT scoped.document_id, scoped.title "
                "FROM ("
                "  SELECT document_id, title FROM documents "
                "  ORDER BY document_id"
                ") AS scoped LIMIT 5",
                "LIMIT 5",
            ),
            (
                "SELECT scoped.document_id "
                "FROM ("
                "  SELECT document_id FROM documents "
                "  ORDER BY document_id"
                ") AS scoped "
                "WHERE scoped.document_id > 1 LIMIT 5",
                "LIMIT 5",
            ),
            (
                "SELECT scoped.document_id + 1 AS shifted "
                "FROM ("
                "  SELECT document_id FROM documents "
                "  ORDER BY document_id"
                ") AS scoped LIMIT 5",
                "LIMIT 5",
            ),
            (
                "WITH selected AS MATERIALIZED ("
                "  SELECT document_id FROM documents "
                "  ORDER BY document_id"
                ") "
                "SELECT document_id FROM selected LIMIT 5",
                "LIMIT 5",
            ),
            (
                "WITH selected AS ("
                "  SELECT document_id FROM documents "
                "  ORDER BY document_id"
                ") "
                "SELECT left_side.document_id "
                "FROM selected AS left_side "
                "JOIN selected AS right_side USING (document_id) "
                "LIMIT 5",
                "LIMIT 5",
            ),
            (
                "SELECT scoped.document_id "
                "FROM ("
                "  SELECT document_id FROM documents "
                "  ORDER BY document_id"
                ") AS scoped LIMIT $row_count",
                "LIMIT $row_count",
            ),
        )
        for sql, limit_sql in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertEqual(compiled.count(limit_sql), 1)

    def test_interactive_pushes_alias_aware_top_k_into_child(self) -> None:
        sql = (
            "SELECT scoped.doc_id AS id, scoped.heading "
            "FROM ("
            "  SELECT document_id AS doc_id, title AS heading "
            "  FROM documents "
            "  WHERE visible"
            ") AS scoped "
            "ORDER BY id DESC NULLS LAST, scoped.heading NULLS FIRST "
            "LIMIT 2 OFFSET 1"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, visible BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(3, 'three', true), (1, 'one', true), "
            "(4, NULL, true), (2, 'two', true), (5, 'hidden', false)"
        )

        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertEqual(compiled.count("ORDER BY"), 2)
        self.assertEqual(compiled.count("LIMIT"), 2)
        self.assertIn("ORDER BY\n    doc_id DESC", compiled)
        self.assertIn("heading NULLS FIRST", compiled)
        self.assertIn("LIMIT 3", compiled)

    def test_interactive_pushes_top_k_into_single_use_cte(self) -> None:
        sql = (
            "WITH selected AS ("
            "  SELECT document_id, title FROM documents WHERE visible"
            ") "
            "SELECT document_id AS id, title "
            "FROM selected "
            "ORDER BY id, title LIMIT 5"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertEqual(compiled.count("ORDER BY"), 2)
        self.assertEqual(compiled.count("LIMIT 5"), 2)

    def test_interactive_top_k_pushdown_respects_barriers(self) -> None:
        cases = (
            (
                "SELECT scoped.document_id, scoped.title "
                "FROM ("
                "  SELECT document_id, title FROM documents WHERE visible"
                ") AS scoped "
                "ORDER BY scoped.document_id LIMIT 5",
                1,
            ),
            (
                "SELECT scoped.document_id, scoped.title "
                "FROM ("
                "  SELECT document_id, title FROM documents WHERE visible"
                ") AS scoped "
                "WHERE scoped.document_id > 1 "
                "ORDER BY scoped.document_id, scoped.title LIMIT 5",
                1,
            ),
            (
                "SELECT scoped.document_id + 1 AS shifted "
                "FROM ("
                "  SELECT document_id FROM documents WHERE visible"
                ") AS scoped "
                "ORDER BY shifted LIMIT 5",
                1,
            ),
            (
                "WITH selected AS MATERIALIZED ("
                "  SELECT document_id, title FROM documents WHERE visible"
                ") "
                "SELECT document_id, title FROM selected "
                "ORDER BY document_id, title LIMIT 5",
                1,
            ),
            (
                "WITH selected AS ("
                "  SELECT document_id, title FROM documents WHERE visible"
                ") "
                "SELECT left_side.document_id, right_side.title "
                "FROM selected AS left_side "
                "JOIN selected AS right_side USING (document_id) "
                "ORDER BY left_side.document_id, right_side.title LIMIT 5",
                1,
            ),
            (
                "SELECT scoped.document_id, scoped.title "
                "FROM ("
                "  SELECT document_id, title FROM documents "
                "  WHERE visible ORDER BY random()"
                ") AS scoped "
                "ORDER BY scoped.document_id, scoped.title LIMIT 5",
                2,
            ),
            (
                "SELECT scoped.document_id, scoped.title "
                "FROM ("
                "  SELECT document_id, title FROM documents WHERE visible"
                ") AS scoped "
                "ORDER BY scoped.document_id, scoped.title "
                "LIMIT $row_count",
                1,
            ),
            (
                "SELECT scoped.shifted, scoped.title "
                "FROM ("
                "  SELECT document_id + 1 AS shifted, title "
                "  FROM documents WHERE visible"
                ") AS scoped "
                "ORDER BY scoped.shifted, scoped.title LIMIT 5",
                1,
            ),
        )
        for sql, expected_orders in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertEqual(
                    compiled.count("ORDER BY"),
                    expected_orders,
                )
                self.assertEqual(compiled.count("LIMIT"), 1)

    def test_interactive_pushes_deterministic_function_predicate(
        self,
    ) -> None:
        sql = (
            "SELECT scoped.document_id, scoped.title "
            "FROM ("
            "  SELECT document_id, title FROM documents "
            "  WHERE title IS NOT NULL"
            ") AS scoped "
            "WHERE lower(trim(scoped.title)) = 'news'"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, ' News '), (2, 'other'), (3, NULL)"
        )

        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertEqual(
            compiled.count("LOWER(TRIM("),
            2,
        )
        self.assertIn(
            "LOWER(TRIM(title)) = 'news'",
            compiled,
        )

    def test_interactive_propagates_deterministic_join_predicate(
        self,
    ) -> None:
        sql = (
            "SELECT document.document_id, element.value "
            "FROM documents AS document "
            "JOIN elements AS element "
            "ON document.document_id = element.owner_document "
            "WHERE abs(document.document_id) = 2"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertIn(
            "ABS(element.owner_document) = 2",
            compiled,
        )

    def test_interactive_propagates_deterministic_left_join_predicate(
        self,
    ) -> None:
        sql = (
            "SELECT document.code, element.value "
            "FROM documents AS document "
            "LEFT JOIN elements AS element "
            "ON document.code = element.owner_code "
            "WHERE lower(document.code) = 'news'"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE documents(code VARCHAR)")
        connection.execute(
            "CREATE TABLE elements(owner_code VARCHAR, value VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES ('NEWS'), ('other')"
        )
        connection.execute(
            "INSERT INTO elements VALUES ('other', 'value')"
        )

        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertIn(
            "LOWER(element.owner_code) = 'news'",
            compiled,
        )

    def test_interactive_pushes_deterministic_group_key_function(
        self,
    ) -> None:
        sql = (
            "SELECT grouped.category, grouped.total "
            "FROM ("
            "  SELECT category, COUNT(*) AS total "
            "  FROM documents GROUP BY category"
            ") AS grouped "
            "WHERE lower(grouped.category) = 'news'"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertEqual(compiled.count("LOWER("), 2)
        self.assertIn("LOWER(category) = 'news'", compiled)

    def test_interactive_function_predicate_safety_barriers(self) -> None:
        cases = (
            (
                "SELECT scoped.document_id "
                "FROM ("
                "  SELECT document_id, title FROM documents "
                "  WHERE title IS NOT NULL"
                ") AS scoped "
                "WHERE mystery(scoped.title)",
                "MYSTERY(",
            ),
            (
                "SELECT scoped.document_id "
                "FROM ("
                "  SELECT document_id, title FROM documents "
                "  WHERE title IS NOT NULL"
                ") AS scoped "
                "WHERE md5(scoped.title) = 'digest'",
                "MD5(",
            ),
            (
                "SELECT scoped.document_id "
                "FROM ("
                "  SELECT document_id, captured_at FROM documents "
                "  WHERE captured_at IS NOT NULL"
                ") AS scoped "
                "WHERE scoped.captured_at <= current_timestamp",
                "CURRENT_TIMESTAMP",
            ),
            (
                "SELECT scoped.document_id "
                "FROM ("
                "  SELECT document_id, title FROM documents "
                "  WHERE title IS NOT NULL"
                ") AS scoped "
                "WHERE scoped.document_id IN ("
                "  SELECT document_id FROM other_documents"
                ")",
                " IN ",
            ),
        )
        for sql, predicate_sql in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertEqual(compiled.count(predicate_sql), 1)

    def test_interactive_hoists_repeated_deterministic_derived_scan(
        self,
    ) -> None:
        sql = (
            "SELECT left_side.document_id AS left_id, "
            "  right_side.document_id AS right_id, left_side.title "
            "FROM ("
            "  SELECT document_id, title FROM documents WHERE visible"
            ") AS left_side "
            "JOIN ("
            "  SELECT document_id, title FROM documents WHERE visible"
            ") AS right_side "
            "ON left_side.document_id = right_side.document_id "
            "ORDER BY left_id, right_id, left_side.title"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, visible BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'one', true), (2, 'two', true), (3, 'three', false)"
        )

        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertIn(
            "_atlas_interactive_shared_1 AS MATERIALIZED",
            compiled,
        )
        self.assertEqual(compiled.count("FROM documents"), 1)
        self.assertEqual(
            compiled.count("FROM _atlas_interactive_shared_1"),
            1,
        )
        self.assertIn(
            "JOIN _atlas_interactive_shared_1 AS right_side",
            compiled,
        )

    def test_interactive_hoist_uses_collision_free_cte_name(self) -> None:
        sql = (
            "WITH _atlas_interactive_shared_1 AS ("
            "  SELECT document_id FROM other_documents"
            ") "
            "SELECT left_side.document_id, right_side.document_id AS right_id "
            "FROM ("
            "  SELECT document_id FROM documents WHERE visible"
            ") AS left_side "
            "JOIN ("
            "  SELECT document_id FROM documents WHERE visible"
            ") AS right_side USING (document_id) "
            "ORDER BY left_side.document_id, right_id"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )
        self.assertIn(
            "_atlas_interactive_shared_2 AS MATERIALIZED",
            compiled,
        )

    def test_interactive_repeated_subquery_hoist_respects_barriers(
        self,
    ) -> None:
        cases = (
            (
                "SELECT left_side.document_id, right_side.title "
                "FROM ("
                "  SELECT document_id, title FROM documents WHERE visible"
                ") AS left_side "
                "JOIN ("
                "  SELECT document_id, title FROM documents WHERE visible"
                ") AS right_side USING (document_id) "
                "ORDER BY left_side.document_id",
            ),
            (
                "SELECT left_side.document_id, right_side.document_id "
                "FROM ("
                "  SELECT document_id FROM documents "
                "  WHERE visible LIMIT 5"
                ") AS left_side "
                "JOIN ("
                "  SELECT document_id FROM documents "
                "  WHERE visible LIMIT 5"
                ") AS right_side USING (document_id) "
                "ORDER BY left_side.document_id, right_side.document_id",
            ),
            (
                "SELECT left_side.document_id, right_side.sample "
                "FROM ("
                "  SELECT document_id, random() AS sample FROM documents"
                ") AS left_side "
                "JOIN ("
                "  SELECT document_id, random() AS sample FROM documents"
                ") AS right_side USING (document_id) "
                "ORDER BY left_side.document_id, right_side.sample",
            ),
            (
                "SELECT left_side.document_id, right_side.document_id "
                "FROM ("
                "  SELECT document_id FROM documents WHERE visible"
                ") AS left_side "
                "JOIN ("
                "  SELECT document_id FROM documents WHERE archived"
                ") AS right_side USING (document_id) "
                "ORDER BY left_side.document_id, right_side.document_id",
            ),
            (
                "SELECT left_side.document_id, right_side.document_id "
                "FROM ("
                "  SELECT document_id FROM read_parquet('docs.parquet')"
                ") AS left_side "
                "JOIN ("
                "  SELECT document_id FROM read_parquet('docs.parquet')"
                ") AS right_side USING (document_id) "
                "ORDER BY left_side.document_id, right_side.document_id",
            ),
        )
        for (sql,) in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertNotIn("_atlas_interactive_shared_", compiled)

    def test_interactive_chooses_cte_materialization_by_reuse(self) -> None:
        cases = (
            (
                "WITH selected AS ("
                "  SELECT document_id, title FROM documents WHERE visible"
                ") "
                "SELECT document_id, title FROM selected "
                "ORDER BY document_id, title",
                "selected AS NOT MATERIALIZED",
            ),
            (
                "WITH selected AS ("
                "  SELECT document_id, title FROM documents WHERE visible"
                ") "
                "SELECT left_side.document_id, right_side.title "
                "FROM selected AS left_side "
                "JOIN selected AS right_side USING (document_id) "
                "ORDER BY left_side.document_id, right_side.title",
                "selected AS MATERIALIZED",
            ),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, visible BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'one', true), (2, 'two', true), (3, 'three', false)"
        )
        for sql, expected_directive in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertIn(expected_directive, compiled)
                self.assertEqual(
                    connection.execute(compiled).fetchall(),
                    connection.execute(sql).fetchall(),
                )

    def test_interactive_preserves_authored_cte_materialization(self) -> None:
        cases = (
            (
                "WITH selected AS MATERIALIZED ("
                "  SELECT document_id, title FROM documents"
                ") "
                "SELECT document_id, title FROM selected "
                "ORDER BY document_id, title",
                "selected AS MATERIALIZED",
            ),
            (
                "WITH selected AS NOT MATERIALIZED ("
                "  SELECT document_id, title FROM documents"
                ") "
                "SELECT left_side.document_id, right_side.title "
                "FROM selected AS left_side "
                "JOIN selected AS right_side USING (document_id) "
                "ORDER BY left_side.document_id, right_side.title",
                "selected AS NOT MATERIALIZED",
            ),
        )
        for sql, expected_directive in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertIn(expected_directive, compiled)

    def test_interactive_cte_materialization_choice_respects_barriers(
        self,
    ) -> None:
        cases = (
            (
                "WITH selected AS ("
                "  SELECT document_id, random() AS sample FROM documents"
                ") "
                "SELECT document_id, sample FROM selected "
                "ORDER BY document_id, sample"
            ),
            (
                "WITH selected AS ("
                "  SELECT document_id FROM read_parquet('docs.parquet')"
                ") "
                "SELECT document_id FROM selected ORDER BY document_id"
            ),
            (
                "WITH selected AS ("
                "  SELECT document_id, title FROM documents"
                ") "
                "SELECT document_id, title FROM selected "
                "ORDER BY document_id"
            ),
            (
                "WITH RECURSIVE selected(document_id) AS ("
                "  SELECT 1 "
                "  UNION ALL "
                "  SELECT document_id + 1 FROM selected "
                "  WHERE document_id < 3"
                ") "
                "SELECT document_id FROM selected ORDER BY document_id"
            ),
        )
        for sql in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertNotIn(" AS MATERIALIZED", compiled)
                self.assertNotIn(" AS NOT MATERIALIZED", compiled)

    def test_interactive_shares_repeated_scalar_macro_expression(
        self,
    ) -> None:
        purpose = InteractiveQueryPurpose(
            scalar_macros=(
                ScalarMacroDefinition(
                    schema_name="macros",
                    macro_name="normalized",
                    parameters=("value",),
                    sql="lower(trim(value))",
                ),
            ),
        )
        sql = (
            "SELECT document_id, "
            "  macros.normalized(title) AS normalized_title, "
            "  macros.normalized(title) AS ordered_title "
            "FROM documents "
            "ORDER BY document_id, normalized_title, ordered_title"
        )
        compiled = compile_catalogue_query(sql, purpose=purpose)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE SCHEMA macros; "
            "CREATE MACRO macros.normalized(value) AS lower(trim(value)); "
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR); "
            "INSERT INTO documents VALUES "
            "(1, ' One '), (2, ' two '), (3, NULL)"
        )

        self.assertEqual(
            connection.execute(compiled).fetchall(),
            connection.execute(sql).fetchall(),
        )
        self.assertIn("CROSS JOIN LATERAL", compiled)
        self.assertIn("_atlas_interactive_expression_1", compiled)
        self.assertEqual(compiled.count("LOWER(TRIM("), 1)

    def test_interactive_shares_each_repeated_scalar_expression(
        self,
    ) -> None:
        sql = (
            "SELECT document_id, "
            "  upper(title) AS upper_title, "
            "  upper(title) AS ordered_upper, "
            "  length(title) AS title_length, "
            "  length(title) AS ordered_length "
            "FROM documents "
            "ORDER BY document_id, upper_title, ordered_upper, "
            "  title_length, ordered_length"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )

        self.assertEqual(
            compiled.count("CROSS JOIN LATERAL"),
            2,
        )
        self.assertEqual(compiled.count("UPPER("), 1)
        self.assertEqual(compiled.count("LENGTH("), 1)

    def test_interactive_expression_share_uses_collision_free_alias(
        self,
    ) -> None:
        sql = (
            "SELECT _atlas_interactive_expression_1.document_id, "
            "  upper(_atlas_interactive_expression_1.title) AS first_title, "
            "  upper(_atlas_interactive_expression_1.title) AS second_title "
            "FROM documents AS _atlas_interactive_expression_1 "
            "ORDER BY document_id, first_title, second_title"
        )
        compiled = compile_catalogue_query(
            sql,
            purpose=InteractiveQueryPurpose(),
        )

        self.assertIn(
            "_atlas_interactive_expression_2",
            compiled,
        )

    def test_interactive_repeated_expression_sharing_respects_barriers(
        self,
    ) -> None:
        cases = (
            (
                "SELECT document_id, upper(title) AS first, "
                "  upper(title) AS second "
                "FROM documents ORDER BY document_id"
            ),
            (
                "SELECT document_id, upper(title) AS first, "
                "  upper(title) AS second "
                "FROM documents WHERE visible "
                "ORDER BY document_id, first, second"
            ),
            (
                "SELECT document_id, random() AS first, random() AS second "
                "FROM documents ORDER BY document_id, first, second"
            ),
            (
                "SELECT document_id, mystery(title) AS first, "
                "  mystery(title) AS second "
                "FROM documents ORDER BY document_id, first, second"
            ),
            (
                "SELECT document_id, upper(title) AS first, "
                "  upper(title) AS second "
                "FROM documents "
                "ORDER BY document_id, first, second LIMIT 2"
            ),
            (
                "SELECT document_id, upper(title) AS first, "
                "  upper(title) AS second "
                "FROM read_parquet('docs.parquet') "
                "ORDER BY document_id, first, second"
            ),
        )
        for sql in cases:
            with self.subTest(sql=sql):
                compiled = compile_catalogue_query(
                    sql,
                    purpose=InteractiveQueryPurpose(),
                )
                self.assertNotIn(
                    "_atlas_interactive_expression_",
                    compiled,
                )

    def test_public_contract_returns_sql_or_actionable_exception(self) -> None:
        compiled = _compile(
            "SELECT document_id, captured_at "
            "FROM documents WHERE captured_at IS NOT NULL"
        )
        self.assertIn(_CHANGED_KEYS, compiled)
        self.assertIn("IS NOT DISTINCT FROM", compiled)

        with self.assertRaises(QueryOptimizationUnavailable) as raised:
            _compile(
                "SELECT document_id FROM documents LIMIT 10"
            )
        self.assertEqual(
            raised.exception.as_dict(),
            {
                "code": OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "message": (
                    "Queries containing limit are not optimized yet."
                ),
                "sql_fragment": "LIMIT 10",
                "documentation_anchor": "supported-subset",
            },
        )

    def test_safe_wildcard_projections_are_equivalent_for_changed_keys(
        self,
    ) -> None:
        cases = (
            "SELECT * FROM documents",
            "SELECT * EXCLUDE (category) FROM documents",
            (
                "SELECT * REPLACE (upper(title) AS title) "
                "FROM documents"
            ),
            (
                "SELECT * RENAME (title AS heading) "
                "FROM documents"
            ),
            (
                "WITH selected AS (SELECT * FROM documents) "
                "SELECT * FROM selected"
            ),
            (
                "WITH selected AS ("
                "  SELECT * EXCLUDE (category) FROM documents"
                ") SELECT * FROM selected"
            ),
            (
                "SELECT document.* FROM documents AS document "
                "JOIN elements AS element USING (document_id)"
            ),
            (
                "SELECT document.* EXCLUDE (category) "
                "FROM documents AS document "
                "JOIN elements AS element USING (document_id)"
            ),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, category VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE elements(document_id INTEGER, value VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'one', 'a'), (2, 'two', 'b'), (3, 'three', 'c')"
        )
        connection.execute(
            "INSERT INTO elements VALUES (1, 'x'), (1, 'y'), (2, 'z')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        for sql in cases:
            with self.subTest(sql=sql):
                compiled = _compile(sql)
                expected = [
                    row
                    for row in connection.execute(sql).fetchall()
                    if row[0] in {1, 3}
                ]
                self.assertCountEqual(
                    connection.execute(compiled).fetchall(),
                    expected,
                )

    def test_ambiguous_or_modified_wildcards_use_structured_fallback(
        self,
    ) -> None:
        cases = (
            (
                "SELECT * FROM documents AS document "
                "JOIN elements AS element USING (document_id)"
            ),
            "SELECT * EXCLUDE (document_id) FROM documents",
            (
                "SELECT * REPLACE (document_id + 1 AS document_id) "
                "FROM documents"
            ),
            (
                "SELECT * RENAME (document_id AS renamed_id) "
                "FROM documents"
            ),
            (
                "SELECT * RENAME (title AS document_id) "
                "FROM documents"
            ),
            "SELECT * ILIKE '%title%' FROM documents",
        )
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(QueryOptimizationUnavailable) as raised:
                    _compile(sql)
                self.assertEqual(
                    raised.exception.code,
                    OptimizationCode.UNSUPPORTED_PROJECTION,
                )
                self.assertEqual(
                    raised.exception.diagnostic.documentation_anchor,
                    "wildcard-projections",
                )

    def test_wildcard_modifiers_preserve_every_composite_key(self) -> None:
        compiled = _compile(
            "SELECT * EXCLUDE (title) FROM documents",
            key_columns=("tenant_id", "document_id"),
        )
        self.assertIn(_CHANGED_KEYS, compiled)

        for excluded_key in ("tenant_id", "document_id"):
            with self.subTest(excluded_key=excluded_key):
                with self.assertRaises(
                    QueryOptimizationUnavailable
                ) as raised:
                    _compile(
                        f"SELECT * EXCLUDE ({excluded_key}) FROM documents",
                        key_columns=("tenant_id", "document_id"),
                    )
                self.assertEqual(
                    raised.exception.code,
                    OptimizationCode.UNSUPPORTED_PROJECTION,
                )

    def test_alias_filter_and_nullable_composite_keys_are_equivalent(self) -> None:
        sql = (
            "SELECT source.tenant_id, source.document_id, source.title "
            "FROM main.documents AS source WHERE source.published"
        )
        compiled = _compile(
            sql,
            key_columns=("tenant_id", "document_id"),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "tenant_id INTEGER, document_id INTEGER, title VARCHAR, "
            "published BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 10, 'included', true), "
            "(1, 11, 'not changed', true), "
            "(2, 20, 'filtered', false), "
            "(NULL, 30, 'null tenant', true)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "tenant_id INTEGER, document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES "
            "(1, 10), (2, 20), (NULL, 30)"
        )
        original = connection.execute(sql).fetchall()
        changed = {(1, 10), (2, 20), (None, 30)}
        expected = [row for row in original if row[:2] in changed]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)

    def test_deterministic_expressions_and_predicates_are_equivalent(self) -> None:
        sql = (
            "SELECT document_id, lower(trim(title)) AS normalized_title, "
            "CAST(score AS BIGINT) AS integer_score, "
            "CASE WHEN active THEN 'yes' ELSE 'no' END AS state "
            "FROM documents "
            "WHERE (length(trim(title)) > 0 AND coalesce(active, false)) "
            "OR NOT active"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, score DOUBLE, active BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, '  ALPHA ', 4.8, true), "
            "(2, '', 2.2, true), "
            "(3, 'Beta', 3.1, false), "
            "(4, ' Gamma ', 5.9, true)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row for row in connection.execute(sql).fetchall() if row[0] in {1, 3}
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)

    def test_known_deterministic_function_catalog_is_executable(self) -> None:
        expressions = (
            "score + 1",
            "abs(score)",
            "ceil(score)",
            "floor(score)",
            "round(score, 1)",
            "greatest(score, 0)",
            "least(score, 10)",
            "nullif(title, '')",
            "upper(title)",
            "substring(title, 1, 2)",
            "concat(title, '!')",
            "concat_ws('-', title, 'x')",
            "replace(title, 'a', 'x')",
            "strpos(title, 'a')",
            "left(title, 2)",
            "right(title, 2)",
            "try_cast(title AS INTEGER)",
            "date_trunc('day', captured_at)",
            "extract(year FROM captured_at)",
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, score DOUBLE, "
            "captured_at TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'alpha', -2.25, TIMESTAMP '2026-07-24 12:34:56'), "
            "(2, '12', 3.75, TIMESTAMP '2025-01-02 03:04:05')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (2)"
        )
        for expression in expressions:
            with self.subTest(expression=expression):
                sql = (
                    f"SELECT document_id, {expression} AS value FROM documents"
                )
                compiled = _compile(sql)
                expected = [
                    row
                    for row in connection.execute(sql).fetchall()
                    if row[0] == 2
                ]
                self.assertCountEqual(
                    connection.execute(compiled).fetchall(),
                    expected,
                )

    def test_nondeterministic_and_unknown_functions_raise(self) -> None:
        cases = (
            (
                "SELECT document_id, random() AS value FROM documents",
                OptimizationCode.NONDETERMINISTIC_FUNCTION,
            ),
            (
                "SELECT document_id, current_timestamp AS value FROM documents",
                OptimizationCode.NONDETERMINISTIC_FUNCTION,
            ),
            (
                "SELECT document_id, uuid() AS value FROM documents",
                OptimizationCode.NONDETERMINISTIC_FUNCTION,
            ),
            (
                "SELECT document_id, nextval('sequence') AS value "
                "FROM documents",
                OptimizationCode.NONDETERMINISTIC_FUNCTION,
            ),
            (
                "SELECT document_id, mystery(title) AS value FROM documents",
                OptimizationCode.UNSUPPORTED_FUNCTION,
            ),
            (
                "SELECT document_id, "
                "macros.text_content(document_id, 1) AS value "
                "FROM documents",
                OptimizationCode.UNSUPPORTED_FUNCTION,
            ),
        )
        for sql, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(QueryOptimizationUnavailable) as raised:
                    _compile(sql)
                self.assertEqual(raised.exception.code, code)

    def test_scalar_macros_expand_recursively_before_validation(self) -> None:
        definitions = (
            ScalarMacroDefinition(
                schema_name="macros",
                macro_name="normalized",
                parameters=("value",),
                sql="lower(trim(value))",
            ),
            ScalarMacroDefinition(
                schema_name="macros",
                macro_name="label",
                parameters=("value",),
                sql="concat(macros.normalized(value), '!')",
            ),
        )
        sql = (
            "SELECT document_id, macros.label(title) AS label "
            "FROM documents"
        )
        compiled = _compile(sql, scalar_macros=definitions)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE SCHEMA macros; "
            "CREATE MACRO macros.normalized(value) AS lower(trim(value)); "
            "CREATE MACRO macros.label(value) "
            "AS concat(macros.normalized(value), '!')"
        )
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES (1, ' One '), (2, ' Two ')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (2)"
        )
        expected = [
            row for row in connection.execute(sql).fetchall() if row[0] == 2
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)
        self.assertNotIn("macros.label", compiled)
        self.assertNotIn("macros.normalized", compiled)

    def test_table_macro_expands_to_key_bounded_derived_scope(self) -> None:
        definition = TableMacroDefinition(
            schema_name="macros",
            macro_name="visible_elements",
            parameters=("minimum_index",),
            parameter_defaults=(("minimum_index", "0"),),
            sql=(
                "SELECT document_id, element_index, value "
                "FROM elements "
                "WHERE element_index >= minimum_index"
            ),
        )
        sql = (
            "SELECT document.document_id, element.value "
            "FROM documents AS document "
            "LEFT JOIN macros.visible_elements(minimum_index := 1) AS element "
            "USING (document_id)"
        )
        compiled = _compile(sql, table_macros=(definition,))
        compiled_with_default = _compile(
            "SELECT document.document_id, element.value "
            "FROM documents AS document "
            "LEFT JOIN macros.visible_elements() AS element "
            "USING (document_id)",
            table_macros=(definition,),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE SCHEMA macros; "
            "CREATE TABLE documents(document_id INTEGER); "
            "CREATE TABLE elements("
            "document_id INTEGER, element_index INTEGER, value VARCHAR); "
            "CREATE MACRO macros.visible_elements(minimum_index := 0) "
            "AS TABLE ("
            "SELECT document_id, element_index, value FROM elements "
            "WHERE element_index >= minimum_index)"
        )
        connection.execute("INSERT INTO documents VALUES (1), (2), (3)")
        connection.execute(
            "INSERT INTO elements VALUES "
            "(1, 0, 'hidden'), (1, 1, 'shown'), (2, 2, 'two')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)
        self.assertNotIn("visible_elements", compiled)
        self.assertNotIn("visible_elements", compiled_with_default)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_unresolved_table_macro_uses_structured_fallback(self) -> None:
        with self.assertRaises(QueryOptimizationUnavailable) as raised:
            _compile(
                "SELECT element.document_id "
                "FROM macros.visible_elements(1) AS element",
                source_table="elements",
            )
        self.assertEqual(
            raised.exception.code,
            OptimizationCode.UNSUPPORTED_FUNCTION,
        )
        self.assertEqual(
            raised.exception.diagnostic.documentation_anchor,
            "macro-expansion",
        )

    def test_using_join_pushes_keys_into_every_scan(self) -> None:
        sql = (
            "SELECT document.document_id, element.element_index "
            "FROM documents AS document "
            "JOIN elements AS element USING (document_id) "
            "WHERE element.visible"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE elements("
            "document_id INTEGER, element_index INTEGER, visible BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO documents VALUES (1, 'one'), (2, 'two'), (NULL, 'n')"
        )
        connection.execute(
            "INSERT INTO elements VALUES "
            "(1, 0, true), (1, 1, false), (2, 0, true), (NULL, 7, true)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1)"
        )
        expected = [
            row for row in connection.execute(sql).fetchall() if row[0] == 1
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_on_join_derives_differently_named_composite_keys(self) -> None:
        sql = (
            "SELECT document.tenant_id, document.document_id, element.value "
            "FROM documents AS document "
            "JOIN elements AS element "
            "ON document.tenant_id = element.owner_id "
            "AND document.document_id = element.document_ref"
        )
        compiled = _compile(
            sql,
            key_columns=("tenant_id", "document_id"),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "tenant_id INTEGER, document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE elements("
            "owner_id INTEGER, document_ref INTEGER, value VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES (1, 10, 'a'), (1, 11, 'b')"
        )
        connection.execute(
            "INSERT INTO elements VALUES (1, 10, 'x'), (1, 11, 'y')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "tenant_id INTEGER, document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1, 11)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[:2] == (1, 11)
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)
        self.assertIn('"owner_id"', compiled)
        self.assertIn('"document_ref"', compiled)

    def test_bound_key_rows_render_as_direct_literals_in_every_scan(self) -> None:
        compiled = _compile(
            "SELECT document.document_id, element.element_index "
            "FROM documents AS document "
            "JOIN elements AS element USING (document_id)",
            key_rows=(("doc-a",), (None,)),
        )

        self.assertNotIn(_CHANGED_KEYS, compiled)
        self.assertEqual(compiled.count("'doc-a'"), 2)
        self.assertEqual(compiled.count("IS NOT DISTINCT FROM NULL"), 2)

    def test_bound_uuid_key_rows_render_as_typed_literals(self) -> None:
        crawl_id = UUID("78b04ba2-0677-58dc-89f4-ad20726f5fb3")
        compiled = _compile(
            "SELECT crawl_id FROM crawls",
            source_table="crawls",
            key_columns=("crawl_id",),
            key_rows=((crawl_id,),),
        )

        self.assertIn(
            "CAST('78b04ba2-0677-58dc-89f4-ad20726f5fb3' AS UUID)",
            compiled,
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE crawls(crawl_id UUID)")
        connection.execute(
            "INSERT INTO crawls VALUES "
            "('78b04ba2-0677-58dc-89f4-ad20726f5fb3'), "
            "('f49bf4bf-3548-5208-b0fe-8a0579d91614')"
        )
        self.assertEqual(connection.execute(compiled).fetchall(), [(crawl_id,)])

    def test_join_lineage_is_transitive_across_three_scans(self) -> None:
        sql = (
            "SELECT document.document_id, attribute.value "
            "FROM documents AS document "
            "JOIN elements AS element "
            "ON document.document_id = element.owner_document "
            "JOIN attributes AS attribute "
            "ON element.owner_document = attribute.document_ref"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER)"
        )
        connection.execute(
            "CREATE TABLE elements(owner_document INTEGER)"
        )
        connection.execute(
            "CREATE TABLE attributes(document_ref INTEGER, value VARCHAR)"
        )
        connection.execute("INSERT INTO documents VALUES (1), (2)")
        connection.execute("INSERT INTO elements VALUES (1), (2)")
        connection.execute(
            "INSERT INTO attributes VALUES (1, 'x'), (2, 'y')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (2)"
        )
        expected = [
            row for row in connection.execute(sql).fetchall() if row[0] == 2
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 3)

    def test_cte_preserves_and_renames_key_lineage(self) -> None:
        sql = (
            "WITH selected AS ("
            "  SELECT document_id AS selected_id, lower(title) AS title "
            "  FROM documents"
            ") "
            "SELECT selected_id AS document_id, title "
            "FROM selected"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES (1, 'ONE'), (2, 'TWO')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (2)"
        )
        expected = [
            row for row in connection.execute(sql).fetchall() if row[0] == 2
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 1)

    def test_explicit_cte_column_list_preserves_positional_lineage(self) -> None:
        sql = (
            "WITH selected(doc_id, label) AS ("
            "  SELECT document_id, title FROM documents"
            ") "
            "SELECT doc_id AS document_id, label FROM selected"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES (1, 'one'), (2, 'two')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (2)"
        )
        expected = [
            row for row in connection.execute(sql).fetchall() if row[0] == 2
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)

    def test_nested_explicit_cte_column_lists_reach_joined_scan(self) -> None:
        sql = (
            "WITH docs(doc_id) AS ("
            "  SELECT document_id FROM documents"
            "), joined(output_id, value) AS ("
            "  SELECT docs.doc_id, element.value "
            "  FROM docs JOIN elements AS element "
            "    ON docs.doc_id = element.owner_document"
            ") "
            "SELECT output_id AS document_id, value FROM joined"
        )
        compiled = _compile(sql)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_invalid_explicit_cte_column_list_uses_fallback(self) -> None:
        with self.assertRaises(QueryOptimizationUnavailable) as raised:
            _compile(
                "WITH selected(doc_id, duplicate) AS ("
                "  SELECT document_id FROM documents"
                ") SELECT doc_id AS document_id FROM selected"
            )
        self.assertEqual(
            raised.exception.code,
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
        )

    def test_union_all_compiles_every_key_preserving_branch(self) -> None:
        sql = (
            "SELECT document_id, title, 'primary' AS source "
            "FROM documents WHERE published "
            "UNION ALL "
            "SELECT document_id, title, 'draft' AS source "
            "FROM documents WHERE NOT published"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, published BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'one', true), (2, 'two', false), (3, 'three', true)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (2)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 2}
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_union_all_by_name_aligns_reordered_and_sparse_branches(
        self,
    ) -> None:
        sql = (
            "SELECT title, document_id, published "
            "FROM documents WHERE published "
            "UNION ALL BY NAME "
            "SELECT document_id, title "
            "FROM documents WHERE NOT published"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, published BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'one', true), (2, 'two', false), (3, 'three', true)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (2)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[1] in {1, 2}
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)
        self.assertIn("UNION ALL BY NAME", compiled)

    def test_union_all_by_name_requires_key_in_every_branch(self) -> None:
        sql = (
            "SELECT document_id, title FROM documents "
            "UNION ALL BY NAME "
            "SELECT title FROM documents"
        )
        with self.assertRaises(QueryOptimizationUnavailable) as raised:
            _compile(sql)
        self.assertEqual(
            raised.exception.code,
            OptimizationCode.KEY_NOT_PRESERVED,
        )
        self.assertEqual(
            raised.exception.diagnostic.documentation_anchor,
            "stable-output-key",
        )

    def test_key_partitioned_set_algebra_is_equivalent(self) -> None:
        queries = (
            (
                "SELECT document_id, title FROM documents WHERE score >= 2 "
                "UNION "
                "SELECT document_id, title FROM documents WHERE score <= 3"
            ),
            (
                "SELECT document_id, title FROM documents WHERE score >= 2 "
                "INTERSECT "
                "SELECT document_id, title FROM documents WHERE score <= 3"
            ),
            (
                "SELECT document_id, title FROM documents WHERE score >= 2 "
                "INTERSECT ALL "
                "SELECT document_id, title FROM documents WHERE score <= 3"
            ),
            (
                "SELECT document_id, title FROM documents WHERE score >= 2 "
                "EXCEPT "
                "SELECT document_id, title FROM documents WHERE score <= 3"
            ),
            (
                "SELECT document_id, title FROM documents WHERE score >= 2 "
                "EXCEPT ALL "
                "SELECT document_id, title FROM documents WHERE score <= 3"
            ),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, score INTEGER)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'a', 1), (1, 'b', 2), (1, 'b', 2), "
            "(2, 'c', 3), (3, 'd', 4), (3, 'e', 2)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        for sql in queries:
            with self.subTest(sql=sql):
                compiled = _compile(sql)
                expected = [
                    row
                    for row in connection.execute(sql).fetchall()
                    if row[0] in {1, 3}
                ]
                self.assertCountEqual(
                    connection.execute(compiled).fetchall(),
                    expected,
                )
                self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_union_by_name_deduplicates_after_name_alignment(self) -> None:
        sql = (
            "SELECT title, document_id, score "
            "FROM documents WHERE score >= 2 "
            "UNION BY NAME "
            "SELECT document_id, title "
            "FROM documents WHERE score <= 3"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, score INTEGER)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'a', 1), (1, 'b', 2), (2, 'c', 3), (3, 'd', 4)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[1] in {1, 3}
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)
        self.assertIn("UNION BY NAME", compiled)

    def test_global_set_ordering_is_membership_neutral(self) -> None:
        sql = (
            "SELECT document_id, title FROM documents WHERE score >= 2 "
            "UNION ALL "
            "SELECT document_id, title FROM documents WHERE score <= 3 "
            "ORDER BY title DESC, document_id"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, score INTEGER)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'a', 1), (1, 'b', 2), (2, 'c', 3), (3, 'd', 4)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)
        self.assertIn("ORDER BY", compiled)

    def test_set_root_ctes_are_propagated_to_each_dependent_leaf(self) -> None:
        sql = (
            "WITH selected AS ("
            "  SELECT document_id, title, score FROM documents"
            "), filtered AS ("
            "  SELECT document_id, title, score "
            "  FROM selected WHERE score >= 2"
            ") "
            "SELECT document_id, title FROM filtered WHERE score <= 3 "
            "UNION ALL "
            "SELECT document_id, title FROM filtered WHERE score >= 3"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, score INTEGER)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'a', 1), (1, 'b', 2), "
            "(2, 'c', 3), (3, 'd', 4)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_set_root_ctes_are_pruned_per_leaf_dependency(self) -> None:
        sql = (
            "WITH selected AS ("
            "  SELECT document_id, title FROM documents"
            "), unused AS ("
            "  SELECT document_id FROM documents"
            ") "
            "SELECT document_id, title FROM documents "
            "UNION ALL "
            "SELECT document_id, title FROM selected"
        )
        compiled = _compile(sql)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)
        self.assertNotIn("unused", compiled.lower())

    def test_recursive_or_unsafe_set_root_ctes_remain_fallback(self) -> None:
        cases = (
            (
                "WITH RECURSIVE walk(document_id) AS ("
                "  SELECT document_id FROM documents "
                "  UNION ALL SELECT document_id FROM walk"
                ") "
                "SELECT document_id FROM walk "
                "UNION ALL SELECT document_id FROM documents",
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            ),
            (
                "WITH selected AS ("
                "  SELECT document_id, random() AS value FROM documents"
                ") "
                "SELECT document_id, value FROM selected "
                "UNION ALL SELECT document_id, 0 AS value FROM documents",
                OptimizationCode.NONDETERMINISTIC_FUNCTION,
            ),
        )
        for sql, code in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(QueryOptimizationUnavailable) as raised:
                    _compile(sql)
                self.assertEqual(raised.exception.code, code)

    def test_global_set_limit_and_offset_remain_fallback(self) -> None:
        cases = (
            (
                "SELECT document_id FROM documents "
                "UNION ALL SELECT document_id FROM documents "
                "LIMIT 2"
            ),
            (
                "SELECT document_id FROM documents "
                "EXCEPT SELECT document_id FROM documents "
                "OFFSET 1"
            ),
        )
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(QueryOptimizationUnavailable) as raised:
                    _compile(sql)
                self.assertEqual(
                    raised.exception.code,
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                )
                self.assertEqual(
                    raised.exception.diagnostic.documentation_anchor,
                    "set-operations",
                )

    def test_positional_union_all_rejects_misaligned_key_columns(self) -> None:
        sql = (
            "SELECT document_id, title FROM documents "
            "UNION ALL "
            "SELECT title, document_id FROM documents"
        )
        with self.assertRaises(QueryOptimizationUnavailable) as raised:
            _compile(sql)
        self.assertEqual(
            raised.exception.code,
            OptimizationCode.KEY_NOT_PRESERVED,
        )
        self.assertEqual(
            raised.exception.diagnostic.documentation_anchor,
            "set-operations",
        )

    def test_nested_union_all_branches_keep_join_scoping(self) -> None:
        sql = (
            "SELECT document.document_id, element.value "
            "FROM documents AS document "
            "JOIN elements AS element USING (document_id) "
            "UNION ALL "
            "SELECT document.document_id, attribute.value "
            "FROM documents AS document "
            "JOIN attributes AS attribute USING (document_id) "
            "UNION ALL "
            "SELECT document_id, title FROM documents"
        )
        compiled = _compile(sql)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 5)

    def test_correlated_scalar_subquery_may_contain_union_all(self) -> None:
        sql = (
            "SELECT document.document_id, ("
            "  SELECT string_agg(fragment, '' ORDER BY position, fragment) "
            "  FROM ("
            "    SELECT element_index AS position, text_direct AS fragment "
            "    FROM elements "
            "    WHERE elements.document_id = document.document_id "
            "    UNION ALL "
            "    SELECT subtree_end_index AS position, text_tail AS fragment "
            "    FROM elements "
            "    WHERE elements.document_id = document.document_id"
            "  ) AS fragments"
            ") AS text "
            "FROM documents AS document"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER); "
            "CREATE TABLE elements("
            "document_id INTEGER, element_index INTEGER, "
            "subtree_end_index INTEGER, text_direct VARCHAR, "
            "text_tail VARCHAR); "
            "INSERT INTO documents VALUES (1), (2), (3); "
            "INSERT INTO elements VALUES "
            "(1, 1, 2, 'a', 'b'), (2, 1, 1, 'c', 'd')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER); "
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertEqual(
            connection.execute(compiled).fetchall(),
            expected,
        )

    def test_unsafe_set_operation_branch_uses_fallback(self) -> None:
        cases = (
            (
                "SELECT document_id FROM documents "
                "UNION ALL SELECT document_id + 1 AS document_id "
                "FROM documents"
            ),
            (
                "SELECT document_id FROM documents "
                "UNION ALL SELECT document_id FROM other_documents"
            ),
        )
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(QueryOptimizationUnavailable):
                    _compile(sql)

    def test_keyed_aggregation_recomputes_complete_changed_groups(self) -> None:
        sql = (
            "SELECT document_id, count(*) AS item_count, "
            "sum(score) AS total_score, min(score) AS minimum_score, "
            "max(score) AS maximum_score, avg(score) AS average_score "
            "FROM documents "
            "GROUP BY document_id "
            "HAVING count(*) > 1"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, score INTEGER)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 10), (1, 20), (2, 7), (3, 2), (3, 4), (3, 6)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (2), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {2, 3}
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)

    def test_group_by_all_infers_exact_stable_key_groups(self) -> None:
        sql = (
            "SELECT document_id, 'summary' AS kind, count(*) AS item_count, "
            "sum(score) AS total_score "
            "FROM documents "
            "GROUP BY ALL "
            "HAVING count(*) > 1"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, score INTEGER)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 10), (1, 20), (2, 7), (3, 2), (3, 4), (3, 6)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (2), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {2, 3}
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)
        self.assertIn("GROUP BY ALL", compiled)

    def test_group_by_all_rejects_extra_or_transformed_dimensions(self) -> None:
        cases = (
            (
                "SELECT document_id, category, count(*) AS item_count "
                "FROM documents GROUP BY ALL"
            ),
            (
                "SELECT document_id + 1 AS document_id, "
                "count(*) AS item_count "
                "FROM documents GROUP BY ALL"
            ),
        )
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(QueryOptimizationUnavailable) as raised:
                    _compile(sql)
                self.assertEqual(
                    raised.exception.code,
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                )
                self.assertEqual(
                    raised.exception.diagnostic.documentation_anchor,
                    "keyed-aggregation",
                )

    def test_algebraic_keyed_aggregates_recompute_complete_groups(self) -> None:
        sql = (
            "SELECT document_id, "
            "bool_and(active) AS all_active, "
            "bool_or(active) AS any_active, "
            "bit_and(flags) AS common_flags, "
            "bit_or(flags) AS combined_flags, "
            "bit_xor(flags) AS toggled_flags, "
            "count_if(active) AS active_count, "
            "median(score) AS median_score, "
            "stddev_pop(score) AS score_stddev, "
            "stddev_samp(score) AS sample_score_stddev, "
            "var_pop(score) AS score_variance, "
            "var_samp(score) AS sample_score_variance, "
            "corr(score, comparison) AS score_correlation, "
            "covar_pop(score, comparison) AS score_covariance, "
            "covar_samp(score, comparison) AS sample_score_covariance "
            "FROM documents GROUP BY document_id"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, active BOOLEAN, flags INTEGER, "
            "score DOUBLE, comparison DOUBLE)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, true, 7, 1.0, 2.0), (1, false, 3, 3.0, 4.0), "
            "(2, true, 6, 2.0, 8.0), "
            "(3, true, 5, 2.0, 7.0), (3, true, 3, 4.0, 5.0), "
            "(3, false, 1, 8.0, 1.0)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)

    def test_value_ordered_aggregates_recompute_deterministically(self) -> None:
        sql = (
            "SELECT document_id, "
            "list(title ORDER BY lower(title), title) AS titles, "
            "list(DISTINCT title ORDER BY title) AS unique_titles, "
            "string_agg(title, ',' ORDER BY lower(title), title) "
            "AS joined_titles, "
            "first(title ORDER BY captured_at, title) AS first_title, "
            "last(title ORDER BY captured_at, title) AS last_title "
            "FROM documents GROUP BY document_id"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, captured_at TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'Beta', TIMESTAMP '2026-01-02'), "
            "(1, 'alpha', TIMESTAMP '2026-01-01'), "
            "(1, 'alpha', TIMESTAMP '2026-01-01'), "
            "(2, 'two', TIMESTAMP '2026-02-01'), "
            "(3, 'zulu', TIMESTAMP '2026-03-02'), "
            "(3, 'echo', TIMESTAMP '2026-03-01')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertEqual(
            sorted(connection.execute(compiled).fetchall()),
            sorted(expected),
        )

    def test_ordered_any_value_recomputes_deterministically(self) -> None:
        sql = (
            "SELECT document_id, "
            "any_value(title ORDER BY captured_at, title) AS chosen_title "
            "FROM documents GROUP BY document_id "
            "ORDER BY document_id"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, captured_at TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, NULL, TIMESTAMP '2026-01-01'), "
            "(1, 'alpha', TIMESTAMP '2026-01-01'), "
            "(1, 'beta', TIMESTAMP '2026-01-01'), "
            "(2, 'two', TIMESTAMP '2026-02-01'), "
            "(3, 'zulu', TIMESTAMP '2026-03-02'), "
            "(3, 'echo', TIMESTAMP '2026-03-01')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)

    def test_deterministic_arg_extremes_recompute_complete_groups(self) -> None:
        sql = (
            "SELECT document_id, "
            "arg_min(title, (score, title)) AS minimum_title, "
            "arg_max(title, (score, title)) AS maximum_title, "
            "arg_max(title, (score, title), 2) AS top_titles "
            "FROM documents GROUP BY document_id "
            "ORDER BY document_id"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, score INTEGER)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'alpha', 10), (1, 'beta', 10), (1, 'gamma', 30), "
            "(2, 'two', 20), "
            "(3, 'echo', 5), (3, 'zulu', 5), (3, 'omega', 9)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)

    def test_order_sensitive_and_row_choice_aggregates_remain_fallback(
        self,
    ) -> None:
        cases = (
            (
                "SELECT document_id, "
                "string_agg(title, ',' ORDER BY captured_at) AS titles "
                "FROM documents GROUP BY document_id"
            ),
            (
                "SELECT document_id, first(title ORDER BY captured_at) AS title "
                "FROM documents GROUP BY document_id"
            ),
            (
                "SELECT document_id, list(title) AS titles "
                "FROM documents GROUP BY document_id"
            ),
            (
                "SELECT document_id, mode(title) AS title "
                "FROM documents GROUP BY document_id"
            ),
            (
                "SELECT document_id, any_value(title) AS title "
                "FROM documents GROUP BY document_id"
            ),
            (
                "SELECT document_id, "
                "any_value(title ORDER BY captured_at) AS title "
                "FROM documents GROUP BY document_id"
            ),
            (
                "SELECT document_id, arg_min(title, score) AS title "
                "FROM documents GROUP BY document_id"
            ),
            (
                "SELECT document_id, arg_max(title, score) AS title "
                "FROM documents GROUP BY document_id"
            ),
        )
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(QueryOptimizationUnavailable) as raised:
                    _compile(sql)
                self.assertEqual(
                    raised.exception.code,
                    OptimizationCode.UNSUPPORTED_FUNCTION,
                )
                self.assertEqual(
                    raised.exception.diagnostic.documentation_anchor,
                    "keyed-aggregation",
                )

    def test_composite_keyed_aggregation_uses_join_lineage(self) -> None:
        sql = (
            "SELECT document.tenant_id, document.document_id, "
            "count(element.element_index) AS element_count "
            "FROM documents AS document "
            "JOIN elements AS element "
            "ON document.tenant_id = element.tenant_id "
            "AND document.document_id = element.document_id "
            "GROUP BY document.tenant_id, document.document_id"
        )
        compiled = _compile(
            sql,
            key_columns=("tenant_id", "document_id"),
        )
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_unsafe_aggregation_shapes_use_fallback(self) -> None:
        cases = (
            "SELECT count(*) AS document_id FROM documents",
            (
                "SELECT document_id, category, count(*) AS item_count "
                "FROM documents GROUP BY document_id, category"
            ),
            (
                "SELECT document_id + 1 AS document_id, count(*) AS item_count "
                "FROM documents GROUP BY document_id + 1"
            ),
            (
                "SELECT document_id, first(title) AS title "
                "FROM documents GROUP BY document_id"
            ),
            (
                "SELECT document_id, string_agg(title, ',') AS titles "
                "FROM documents GROUP BY document_id"
            ),
        )
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(QueryOptimizationUnavailable):
                    _compile(sql)

    def test_plain_distinct_is_equivalent_for_changed_keys(self) -> None:
        sql = (
            "SELECT DISTINCT document_id, category "
            "FROM documents WHERE category IS NOT NULL"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, category VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'a'), (1, 'a'), (1, 'b'), "
            "(2, 'a'), (2, 'a'), (3, NULL)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)

    def test_distinct_inside_key_preserving_scope_remains_bounded(self) -> None:
        sql = (
            "WITH unique_elements AS ("
            "  SELECT DISTINCT document_id, value FROM elements"
            ") "
            "SELECT document.document_id, unique_elements.value "
            "FROM documents AS document "
            "JOIN unique_elements USING (document_id)"
        )
        compiled = _compile(sql)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_distinct_on_chooses_one_deterministic_row_per_key(self) -> None:
        sql = (
            "SELECT DISTINCT ON (document_id) "
            "document_id, upper(title) AS title, captured_at "
            "FROM documents "
            "ORDER BY captured_at DESC, title"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, captured_at TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'older', TIMESTAMP '2026-01-01'), "
            "(1, 'newer', TIMESTAMP '2026-01-02'), "
            "(1, 'newer', TIMESTAMP '2026-01-02'), "
            "(2, 'two', TIMESTAMP '2026-02-01'), "
            "(3, 'early', TIMESTAMP '2026-03-01'), "
            "(3, 'late', TIMESTAMP '2026-03-02')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)

    def test_unproven_distinct_on_uses_fallback(self) -> None:
        cases = (
            (
                "SELECT DISTINCT ON (document_id) document_id, title "
                "FROM documents ORDER BY document_id, captured_at DESC"
            ),
            (
                "SELECT DISTINCT ON (category) document_id, category "
                "FROM documents ORDER BY category, document_id"
            ),
            (
                "SELECT DISTINCT ON (document_id) * "
                "FROM documents ORDER BY document_id"
            ),
        )
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(QueryOptimizationUnavailable) as raised:
                    _compile(sql)
                self.assertEqual(
                    raised.exception.code,
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                )
                self.assertEqual(
                    raised.exception.diagnostic.documentation_anchor,
                    "key-local-distinct",
                )

    def test_unsupported_shape_inside_cte_uses_fallback(self) -> None:
        with self.assertRaises(QueryOptimizationUnavailable) as raised:
            _compile(
                "WITH recent AS ("
                "  SELECT document_id FROM documents LIMIT 10"
                ") SELECT document_id FROM recent"
            )
        self.assertEqual(
            raised.exception.code,
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
        )

    def test_ordering_without_limit_is_key_locally_equivalent(self) -> None:
        sql = (
            "SELECT document_id, score "
            "FROM documents "
            "ORDER BY score DESC NULLS LAST, document_id"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, score INTEGER)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 10), (2, NULL), (3, 30), (4, 20)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)
        self.assertIn("ORDER BY", compiled)

    def test_ordering_inside_key_preserving_cte_remains_bounded(self) -> None:
        sql = (
            "WITH ranked_input AS ("
            "  SELECT document_id, score "
            "  FROM documents ORDER BY score DESC"
            ") "
            "SELECT document_id, score FROM ranked_input"
        )
        compiled = _compile(sql)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 1)
        self.assertIn("ORDER BY", compiled)

    def test_ordering_with_limit_or_offset_uses_fallback(self) -> None:
        cases = (
            "SELECT document_id FROM documents ORDER BY captured_at LIMIT 10",
            "SELECT document_id FROM documents ORDER BY captured_at OFFSET 10",
            (
                "WITH recent AS ("
                "  SELECT document_id FROM documents "
                "  ORDER BY captured_at LIMIT 10"
                ") SELECT document_id FROM recent"
            ),
        )
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(QueryOptimizationUnavailable):
                    _compile(sql)

    def test_key_partitioned_windows_and_qualify_are_equivalent(self) -> None:
        sql = (
            "SELECT document_id, score, "
            "sum(score) OVER ("
            "  PARTITION BY document_id "
            "  ORDER BY score "
            "  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
            ") AS running_score, "
            "rank() OVER ("
            "  PARTITION BY document_id ORDER BY score DESC"
            ") AS score_rank "
            "FROM documents "
            "QUALIFY score_rank <= 2 "
            "ORDER BY document_id, score"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, score INTEGER)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 10), (1, 20), (1, 30), "
            "(2, 5), (2, 15), (3, 7)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)

    def test_composite_key_window_allows_additional_partitions(self) -> None:
        sql = (
            "SELECT tenant_id, document_id, category, score, "
            "max(score) OVER ("
            "  PARTITION BY tenant_id, document_id, category"
            ") AS category_max "
            "FROM documents"
        )
        compiled = _compile(
            sql,
            key_columns=("tenant_id", "document_id"),
        )
        self.assertEqual(compiled.count(_CHANGED_KEYS), 1)

    def test_deterministic_row_number_window_is_key_local(self) -> None:
        sql = (
            "SELECT document_id, title, captured_at, "
            "row_number() OVER ("
            "  PARTITION BY document_id "
            "  ORDER BY captured_at DESC, title"
            ") AS position "
            "FROM documents "
            "QUALIFY position = 1 "
            "ORDER BY document_id"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, captured_at TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'old', TIMESTAMP '2026-01-01'), "
            "(1, 'new', TIMESTAMP '2026-01-02'), "
            "(2, 'two', TIMESTAMP '2026-02-01'), "
            "(3, 'same', TIMESTAMP '2026-03-01'), "
            "(3, 'same', TIMESTAMP '2026-03-01')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)

    def test_named_window_is_resolved_before_key_local_proof(self) -> None:
        sql = (
            "SELECT document_id, title, captured_at, "
            "row_number() OVER latest AS position "
            "FROM documents "
            "WINDOW latest AS ("
            "  PARTITION BY document_id "
            "  ORDER BY captured_at DESC, title"
            ") "
            "QUALIFY position = 1 "
            "ORDER BY document_id"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, captured_at TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'old', TIMESTAMP '2026-01-01'), "
            "(1, 'new', TIMESTAMP '2026-01-02'), "
            "(2, 'two', TIMESTAMP '2026-02-01'), "
            "(3, 'a', TIMESTAMP '2026-03-01'), "
            "(3, 'b', TIMESTAMP '2026-03-02')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)
        self.assertNotIn("WINDOW latest", compiled)
        self.assertIn("PARTITION BY document_id", compiled)

    def test_named_window_still_requires_complete_key_partition(self) -> None:
        sql = (
            "SELECT document_id, category, "
            "sum(score) OVER category_scores AS category_score "
            "FROM documents "
            "WINDOW category_scores AS (PARTITION BY category)"
        )
        with self.assertRaises(QueryOptimizationUnavailable) as raised:
            _compile(sql)
        self.assertEqual(
            raised.exception.code,
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
        )

    def test_named_window_inheritance_is_resolved_transitively(self) -> None:
        sql = (
            "SELECT document_id, title, "
            "row_number() OVER ordered AS position "
            "FROM documents "
            "WINDOW keyed AS (PARTITION BY document_id), "
            "ordered AS (keyed ORDER BY title) "
            "ORDER BY document_id, title"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'b'), (1, 'a'), (2, 'two'), (3, 'z'), (3, 'x')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)
        self.assertNotIn("WINDOW ", compiled)

    def test_invalid_named_window_inheritance_fails_closed(self) -> None:
        cases = (
            (
                "SELECT document_id, "
                "sum(score) OVER child AS total "
                "FROM documents "
                "WINDOW parent AS (PARTITION BY document_id), "
                "child AS (parent PARTITION BY category)",
                OptimizationCode.INVALID_QUERY,
            ),
            (
                "SELECT document_id, "
                "sum(score) OVER first_window AS total "
                "FROM documents "
                "WINDOW first_window AS (second_window), "
                "second_window AS (first_window)",
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            ),
        )
        for sql, expected_code in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(
                    QueryOptimizationUnavailable
                ) as raised:
                    _compile(sql)
                self.assertEqual(
                    raised.exception.code,
                    expected_code,
                )

    def test_value_windows_share_one_deterministic_key_order(self) -> None:
        window = (
            "PARTITION BY document_id "
            "ORDER BY captured_at, title"
        )
        sql = (
            "SELECT document_id, title, captured_at, "
            f"lag(title, 1, 'none') OVER ({window}) AS previous_title, "
            f"lead(title, 1, 'none') OVER ({window}) AS next_title, "
            f"first_value(title) OVER ({window}) AS first_title, "
            f"nth_value(title, 2) OVER ({window}) AS second_title "
            "FROM documents ORDER BY document_id, captured_at, title"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, captured_at TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'a', TIMESTAMP '2026-01-01'), "
            "(1, 'b', TIMESTAMP '2026-01-02'), "
            "(1, 'c', TIMESTAMP '2026-01-03'), "
            "(2, 'two', TIMESTAMP '2026-02-01'), "
            "(3, 'x', TIMESTAMP '2026-03-01'), "
            "(3, 'y', TIMESTAMP '2026-03-02')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)

    def test_deterministic_ntile_window_is_key_local(self) -> None:
        sql = (
            "SELECT document_id, score, title, "
            "ntile(3) OVER ("
            "  PARTITION BY document_id ORDER BY score, title"
            ") AS bucket "
            "FROM documents "
            "ORDER BY document_id, score, title, bucket"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, score INTEGER, title VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 10, 'a'), (1, 10, 'a'), (1, 10, 'b'), "
            "(1, 20, 'c'), (1, 30, 'd'), "
            "(2, 5, 'two'), "
            "(3, 1, 'x'), (3, 2, 'y'), (3, 3, 'z')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)

    def test_global_cross_key_and_unproven_windows_use_fallback(self) -> None:
        cases = (
            (
                "SELECT document_id, "
                "sum(score) OVER () AS global_score FROM documents"
            ),
            (
                "SELECT document_id, category, "
                "sum(score) OVER (PARTITION BY category) AS category_score "
                "FROM documents"
            ),
            (
                "SELECT tenant_id, document_id, "
                "sum(score) OVER (PARTITION BY document_id) AS score "
                "FROM documents",
                ("tenant_id", "document_id"),
            ),
            (
                "SELECT document_id, title, "
                "row_number() OVER ("
                "  PARTITION BY document_id ORDER BY score"
                ") AS position FROM documents"
            ),
            (
                "SELECT document_id, title, "
                "lag(title) OVER ("
                "  PARTITION BY document_id ORDER BY captured_at"
                ") AS previous_title FROM documents"
            ),
            (
                "SELECT document_id, title, "
                "lag(title, offset_rows) OVER ("
                "  PARTITION BY document_id "
                "  ORDER BY captured_at, title"
                ") AS previous_title FROM documents"
            ),
            (
                "SELECT document_id, title, "
                "row_number() OVER ("
                "  PARTITION BY document_id "
                "  ORDER BY captured_at, title"
                ") AS chronological_position, "
                "row_number() OVER ("
                "  PARTITION BY document_id "
                "  ORDER BY score, title"
                ") AS score_position "
                "FROM documents"
            ),
            (
                "SELECT document_id, title, "
                "ntile(3) OVER ("
                "  PARTITION BY document_id ORDER BY score"
                ") AS bucket FROM documents"
            ),
            (
                "SELECT document_id, score, "
                "ntile(bucket_count) OVER ("
                "  PARTITION BY document_id ORDER BY score"
                ") AS bucket FROM documents"
            ),
            (
                "SELECT document_id, score, "
                "ntile(3) OVER (PARTITION BY document_id) AS bucket "
                "FROM documents"
            ),
            (
                "SELECT document_id, score, "
                "ntile(0) OVER ("
                "  PARTITION BY document_id ORDER BY score"
                ") AS bucket FROM documents"
            ),
            (
                "SELECT document_id, score, "
                "ntile(3) OVER (ORDER BY document_id, score) AS bucket "
                "FROM documents"
            ),
        )
        for case in cases:
            sql = case[0]
            key_columns = case[1] if len(case) > 1 else ("document_id",)
            with self.subTest(sql=sql):
                with self.assertRaises(QueryOptimizationUnavailable):
                    _compile(sql, key_columns=key_columns)

    def test_projection_unnest_is_equivalent_for_bounded_rows(self) -> None:
        sql = (
            "SELECT document_id, unnest(tags) AS tag "
            "FROM documents ORDER BY document_id, tag"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, tags VARCHAR[])"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, ['a', 'b']), (2, []), (3, ['c'])"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (2)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 2}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)

    def test_cross_and_lateral_unnest_are_row_local(self) -> None:
        queries = (
            (
                "SELECT document.document_id, tag.value "
                "FROM documents AS document "
                "CROSS JOIN UNNEST(document.tags) AS tag(value)"
            ),
            (
                "SELECT document.document_id, tag.value "
                "FROM documents AS document, "
                "LATERAL UNNEST(document.tags) AS tag(value)"
            ),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, tags VARCHAR[])"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, ['a', 'b']), (2, ['c']), (3, [])"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (2), (3)"
        )
        for sql in queries:
            with self.subTest(sql=sql):
                compiled = _compile(sql)
                expected = [
                    row
                    for row in connection.execute(sql).fetchall()
                    if row[0] in {2, 3}
                ]
                self.assertCountEqual(
                    connection.execute(compiled).fetchall(),
                    expected,
                )
                self.assertEqual(compiled.count(_CHANGED_KEYS), 1)

    def test_row_local_lateral_selects_are_key_locally_equivalent(self) -> None:
        queries = (
            (
                "SELECT document.document_id, nested.heading "
                "FROM documents AS document "
                "CROSS JOIN LATERAL ("
                "  SELECT upper(document.title) AS heading"
                ") AS nested "
                "ORDER BY document.document_id"
            ),
            (
                "SELECT document.document_id, nested.original_title "
                "FROM documents AS document "
                "LEFT JOIN LATERAL ("
                "  SELECT document.title AS original_title "
                "  WHERE document.active"
                ") AS nested ON true "
                "ORDER BY document.document_id"
            ),
            (
                "SELECT document.document_id, normalized.heading "
                "FROM documents AS document "
                "CROSS JOIN LATERAL ("
                "  SELECT upper(document.title) AS heading"
                ") AS projected "
                "CROSS JOIN LATERAL ("
                "  SELECT lower(projected.heading) AS heading"
                ") AS normalized "
                "ORDER BY document.document_id"
            ),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, title VARCHAR, active BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'one', true), (2, 'two', false), (3, 'three', true)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (2)"
        )
        for sql in queries:
            with self.subTest(sql=sql):
                compiled = _compile(sql)
                expected = [
                    row
                    for row in connection.execute(sql).fetchall()
                    if row[0] in {1, 2}
                ]
                self.assertEqual(
                    connection.execute(compiled).fetchall(),
                    expected,
                )
                self.assertEqual(compiled.count(_CHANGED_KEYS), 1)

    def test_unbounded_table_functions_and_lateral_queries_use_fallback(
        self,
    ) -> None:
        cases = (
            (
                "SELECT document.document_id, value "
                "FROM documents AS document "
                "CROSS JOIN range(10) AS generated(value)"
            ),
            (
                "SELECT document.document_id, nested.value "
                "FROM documents AS document "
                "CROSS JOIN LATERAL ("
                "  SELECT element.value "
                "  FROM elements AS element "
                "  WHERE element.document_id = document.document_id"
                ") AS nested"
            ),
            (
                "SELECT document.document_id, nested.value "
                "FROM documents AS document "
                "CROSS JOIN LATERAL ("
                "  SELECT random() AS value"
                ") AS nested"
            ),
            (
                "SELECT document.document_id, nested.value "
                "FROM documents AS document "
                "CROSS JOIN LATERAL ("
                "  SELECT unnest(document.tags) AS value"
                ") AS nested"
            ),
            (
                "SELECT document_id, "
                "unnest((SELECT tags FROM other_documents LIMIT 1)) AS tag "
                "FROM documents"
            ),
        )
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(QueryOptimizationUnavailable):
                    _compile(sql)

    def test_dynamic_and_external_relations_are_explicitly_unmanaged(
        self,
    ) -> None:
        cases = (
            (
                "SELECT document_id "
                "FROM read_parquet('s3://bucket/documents.parquet')",
                "READ_PARQUET",
            ),
            (
                "SELECT document_id FROM read_csv_auto('documents.csv')",
                "READ_CSV_AUTO",
            ),
            (
                "SELECT document_id "
                "FROM postgres_scan('secret', 'public', 'documents')",
                "POSTGRES_SCAN",
            ),
            (
                "SELECT document_id "
                "FROM query('SELECT document_id FROM documents')",
                "QUERY",
            ),
            (
                "SELECT document_id FROM query_table('documents')",
                "QUERY_TABLE",
            ),
        )
        for sql, fragment in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(QueryOptimizationUnavailable) as raised:
                    _compile(sql)
                self.assertEqual(
                    raised.exception.code,
                    OptimizationCode.UNMANAGED_RELATION,
                )
                self.assertEqual(
                    raised.exception.diagnostic.documentation_anchor,
                    "managed-relations",
                )
                self.assertEqual(
                    raised.exception.diagnostic.sql_fragment,
                    f"{fragment}(...)",
                )

    def test_other_table_functions_keep_generic_fallback(self) -> None:
        with self.assertRaises(QueryOptimizationUnavailable) as raised:
            _compile(
                "SELECT document.document_id, generated.value "
                "FROM documents AS document "
                "CROSS JOIN range(10) AS generated(value)"
            )
        self.assertEqual(
            raised.exception.code,
            OptimizationCode.UNSUPPORTED_FUNCTION,
        )

    def test_derived_table_key_lineage_reaches_joined_scan(self) -> None:
        sql = (
            "SELECT scoped.doc_id AS document_id, element.element_index "
            "FROM ("
            "  SELECT document_id AS doc_id FROM documents"
            ") AS scoped "
            "JOIN elements AS element "
            "ON scoped.doc_id = element.owner_document"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE documents(document_id INTEGER)")
        connection.execute(
            "CREATE TABLE elements("
            "owner_document INTEGER, element_index INTEGER)"
        )
        connection.execute("INSERT INTO documents VALUES (1), (2)")
        connection.execute(
            "INSERT INTO elements VALUES (1, 10), (2, 20)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1)"
        )
        expected = [
            row for row in connection.execute(sql).fetchall() if row[0] == 1
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_nested_ctes_propagate_transitive_join_lineage(self) -> None:
        sql = (
            "WITH docs AS ("
            "  SELECT document_id AS doc_id FROM documents"
            "), joined AS ("
            "  SELECT docs.doc_id, element.element_index "
            "  FROM docs "
            "  JOIN elements AS element "
            "    ON docs.doc_id = element.owner_document"
            ") "
            "SELECT doc_id AS document_id, element_index FROM joined"
        )
        compiled = _compile(sql)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_scope_that_transforms_or_drops_key_uses_fallback(self) -> None:
        cases = (
            (
                "WITH scoped AS ("
                "  SELECT document_id + 1 AS doc_id FROM documents"
                ") SELECT doc_id AS document_id FROM scoped",
                OptimizationCode.KEY_NOT_PRESERVED,
            ),
            (
                "SELECT scoped.document_id "
                "FROM (SELECT title AS document_id FROM documents) AS scoped",
                OptimizationCode.KEY_NOT_PRESERVED,
            ),
        )
        for sql, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(QueryOptimizationUnavailable) as raised:
                    _compile(sql)
                self.assertEqual(raised.exception.code, code)

    def test_recursive_cte_is_not_optimized(self) -> None:
        sql = (
            "WITH RECURSIVE walk(document_id) AS ("
            "  SELECT document_id FROM documents "
            "  UNION ALL "
            "  SELECT document_id FROM walk"
            ") SELECT document_id FROM walk"
        )
        with self.assertRaises(QueryOptimizationUnavailable) as raised:
            _compile(sql)
        self.assertEqual(
            raised.exception.code,
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
        )

    def test_left_join_scopes_nullable_side_without_losing_unmatched_rows(
        self,
    ) -> None:
        sql = (
            "SELECT document.document_id, element.value "
            "FROM documents AS document "
            "LEFT JOIN elements AS element "
            "ON document.document_id = element.owner_document "
            "AND element.visible"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE elements("
            "owner_document INTEGER, value VARCHAR, visible BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO documents VALUES (1, 'one'), (2, 'two'), (3, 'three')"
        )
        connection.execute(
            "INSERT INTO elements VALUES "
            "(1, 'shown', true), (1, 'hidden', false), (2, 'two', true)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_left_join_using_is_equivalent_through_derived_table(self) -> None:
        sql = (
            "WITH scoped AS ("
            "  SELECT document_id, title FROM documents"
            ") "
            "SELECT scoped.document_id, element.value "
            "FROM scoped "
            "LEFT JOIN elements AS element USING (document_id)"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE elements(document_id INTEGER, value VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES (1, 'one'), (2, 'two')"
        )
        connection.execute("INSERT INTO elements VALUES (1, 'value')")
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (2)"
        )
        expected = [
            row for row in connection.execute(sql).fetchall() if row[0] == 2
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)

    def test_right_join_preserves_changed_driving_rows(self) -> None:
        sql = (
            "SELECT document.document_id, element.value "
            "FROM elements AS element "
            "RIGHT JOIN documents AS document "
            "ON element.owner_document = document.document_id "
            "AND element.visible"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE elements("
            "owner_document INTEGER, value VARCHAR, visible BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO documents VALUES (1, 'one'), (2, 'two'), (3, 'three')"
        )
        connection.execute(
            "INSERT INTO elements VALUES "
            "(1, 'shown', true), (1, 'hidden', false), (2, 'two', true)"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertCountEqual(connection.execute(compiled).fetchall(), expected)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_right_join_rejects_nullable_driver_or_unbounded_input(
        self,
    ) -> None:
        cases = (
            (
                "SELECT document.document_id, element.value "
                "FROM documents AS document "
                "RIGHT JOIN elements AS element USING (document_id)",
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            ),
            (
                "SELECT document.document_id, element.value "
                "FROM elements AS element "
                "RIGHT JOIN documents AS document "
                "ON element.category = document.category",
                OptimizationCode.UNBOUNDED_RELATION,
            ),
        )
        for sql, code in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(QueryOptimizationUnavailable) as raised:
                    _compile(sql)
                self.assertEqual(raised.exception.code, code)

    def test_full_join_using_recomputes_complete_key_partitions(self) -> None:
        sql = (
            "SELECT document_id, document.title, element.value "
            "FROM documents AS document "
            "FULL JOIN elements AS element USING (document_id) "
            "ORDER BY document_id, title, value"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE elements(document_id INTEGER, value VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'one'), (2, 'two'), (3, 'three')"
        )
        connection.execute(
            "INSERT INTO elements VALUES "
            "(1, 'a'), (1, 'b'), (2, 'two'), (4, 'right only')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_composite_key_full_join_using_is_key_local(self) -> None:
        sql = (
            "SELECT tenant_id, document_id, document.title, element.value "
            "FROM documents AS document "
            "FULL JOIN elements AS element "
            "USING (tenant_id, document_id) "
            "ORDER BY tenant_id, document_id, title, value"
        )
        compiled = _compile(
            sql,
            key_columns=("tenant_id", "document_id"),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "tenant_id INTEGER, document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE elements("
            "tenant_id INTEGER, document_id INTEGER, value VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 10, 'one'), (1, 11, 'eleven'), (2, 20, 'twenty')"
        )
        connection.execute(
            "INSERT INTO elements VALUES "
            "(1, 10, 'a'), (1, 10, 'b'), (2, 21, 'right only')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "tenant_id INTEGER, document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES "
            "(1, 10), (1, 11)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[:2] in {(1, 10), (1, 11)}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_full_join_merged_key_propagates_through_cte(self) -> None:
        sql = (
            "WITH combined AS ("
            "  SELECT document_id, document.title, element.value "
            "  FROM documents AS document "
            "  FULL JOIN elements AS element USING (document_id)"
            ") "
            "SELECT document_id, title, value FROM combined"
        )
        compiled = _compile(sql)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_root_full_join_on_accepts_proven_coalesced_key(self) -> None:
        sql = (
            "SELECT "
            "coalesce(document.document_id, element.owner_document) "
            "AS document_id, "
            "document.title, element.value "
            "FROM documents AS document "
            "FULL JOIN elements AS element "
            "ON document.document_id = element.owner_document "
            "ORDER BY document_id, title, value"
        )
        compiled = _compile(sql)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents(document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE elements("
            "owner_document INTEGER, value VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 'one'), (2, 'two'), (3, 'three')"
        )
        connection.execute(
            "INSERT INTO elements VALUES "
            "(1, 'a'), (1, 'b'), (2, 'two'), (4, 'right only')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1), (3)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[0] in {1, 3}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_composite_full_join_on_coalesces_each_key(self) -> None:
        sql = (
            "SELECT "
            "coalesce(document.tenant_id, element.owner_tenant) "
            "AS tenant_id, "
            "coalesce(document.document_id, element.owner_document) "
            "AS document_id, "
            "document.title, element.value "
            "FROM documents AS document "
            "FULL JOIN elements AS element "
            "ON document.tenant_id = element.owner_tenant "
            "AND document.document_id = element.owner_document "
            "ORDER BY tenant_id, document_id, title, value"
        )
        compiled = _compile(
            sql,
            key_columns=("tenant_id", "document_id"),
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "tenant_id INTEGER, document_id INTEGER, title VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE elements("
            "owner_tenant INTEGER, owner_document INTEGER, value VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "(1, 10, 'one'), (1, 11, 'eleven'), (2, 20, 'twenty')"
        )
        connection.execute(
            "INSERT INTO elements VALUES "
            "(1, 10, 'a'), (1, 10, 'b'), (2, 21, 'right only')"
        )
        connection.execute(
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "tenant_id INTEGER, document_id INTEGER)"
        )
        connection.execute(
            "INSERT INTO _atlas_materialization_changed_keys VALUES "
            "(1, 10), (1, 11)"
        )
        expected = [
            row
            for row in connection.execute(sql).fetchall()
            if row[:2] in {(1, 10), (1, 11)}
        ]
        self.assertEqual(connection.execute(compiled).fetchall(), expected)
        self.assertEqual(compiled.count(_CHANGED_KEYS), 2)

    def test_full_join_requires_merged_complete_key_projection(self) -> None:
        cases = (
            (
                "SELECT document.document_id, document.title, element.value "
                "FROM documents AS document "
                "FULL JOIN elements AS element USING (document_id)",
                ("document_id",),
            ),
            (
                "SELECT greatest(document.document_id, element.document_id) "
                "AS document_id, document.title, element.value "
                "FROM documents AS document "
                "FULL JOIN elements AS element "
                "ON document.document_id = element.document_id",
                ("document_id",),
            ),
            (
                "SELECT tenant_id, document_id, document.title "
                "FROM documents AS document "
                "FULL JOIN elements AS element USING (document_id)",
                ("tenant_id", "document_id"),
            ),
            (
                "WITH combined AS ("
                "  SELECT "
                "  coalesce(document.document_id, element.document_id) "
                "  AS document_id, document.title, element.value "
                "  FROM documents AS document "
                "  FULL JOIN elements AS element "
                "  ON document.document_id = element.document_id"
                ") "
                "SELECT document_id, title, value FROM combined",
                ("document_id",),
            ),
        )
        for sql, key_columns in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(
                    QueryOptimizationUnavailable
                ) as raised:
                    _compile(sql, key_columns=key_columns)
                self.assertEqual(
                    raised.exception.code,
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                )

    def test_full_join_coalesce_does_not_invent_join_lineage(self) -> None:
        sql = (
            "SELECT coalesce(document.document_id, element.document_id) "
            "AS document_id, document.title, element.value "
            "FROM documents AS document "
            "FULL JOIN elements AS element "
            "ON document.category = element.category"
        )
        with self.assertRaises(QueryOptimizationUnavailable) as raised:
            _compile(sql)
        self.assertEqual(
            raised.exception.code,
            OptimizationCode.UNBOUNDED_RELATION,
        )

    def test_unsafe_and_unbounded_joins_raise_for_caller_fallback(self) -> None:
        cases = (
            (
                "SELECT document.document_id, element.value "
                "FROM documents AS document "
                "JOIN elements AS element "
                "ON document.category = element.category",
                OptimizationCode.UNBOUNDED_RELATION,
            ),
            (
                "SELECT document.document_id, category.name "
                "FROM categories AS category "
                "LEFT JOIN documents AS document "
                "ON category.document_id = document.document_id",
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            ),
            (
                "WITH docs AS ("
                "  SELECT document_id FROM documents"
                ") "
                "SELECT docs.document_id, category.name "
                "FROM categories AS category "
                "LEFT JOIN docs "
                "ON category.document_id = docs.document_id",
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            ),
            (
                "SELECT document.document_id, element.value "
                "FROM documents AS document "
                "LEFT JOIN elements AS element "
                "ON document.category = element.category",
                OptimizationCode.UNBOUNDED_RELATION,
            ),
        )
        for sql, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(QueryOptimizationUnavailable) as raised:
                    _compile(sql)
                self.assertEqual(raised.exception.code, code)

    def test_materialized_driver_anchor_bounds_dependent_join_scans(self) -> None:
        sql = (
            "WITH selected AS MATERIALIZED ("
            "  SELECT document_id, category FROM documents"
            ") "
            "SELECT selected.document_id, element.value "
            "FROM selected "
            "LEFT JOIN elements AS element "
            "ON (((element.category = selected.category)))"
        )

        compiled = _compile(sql)

        self.assertEqual(compiled.count(_CHANGED_KEYS), 1)
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE documents("
            "document_id INTEGER, category VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE elements(category VARCHAR, value VARCHAR)"
        )
        connection.execute(
            "INSERT INTO documents VALUES (1, 'a'), (2, 'b'); "
            "INSERT INTO elements VALUES ('a', 'one'), ('b', 'two'); "
            "CREATE TEMP TABLE _atlas_materialization_changed_keys("
            "document_id INTEGER); "
            "INSERT INTO _atlas_materialization_changed_keys VALUES (1)"
        )
        self.assertEqual(connection.execute(compiled).fetchall(), [(1, "one")])

        with self.assertRaises(QueryOptimizationUnavailable) as raised:
            _compile(sql.replace("AS MATERIALIZED", "AS NOT MATERIALIZED"))
        self.assertEqual(
            raised.exception.code,
            OptimizationCode.UNBOUNDED_RELATION,
        )

    def test_key_alias_and_reserved_relation_raise(self) -> None:
        with self.assertRaises(QueryOptimizationUnavailable) as aliased:
            _compile("SELECT document_id AS id, title FROM documents")
        self.assertEqual(
            aliased.exception.code,
            OptimizationCode.KEY_NOT_PRESERVED,
        )
        with self.assertRaises(QueryOptimizationUnavailable) as reserved:
            _compile(
                "SELECT document_id "
                "FROM _atlas_materialization_changed_keys",
                source_table="_atlas_materialization_changed_keys",
            )
        self.assertEqual(
            reserved.exception.code,
            OptimizationCode.RESERVED_RELATION,
        )


if __name__ == "__main__":
    unittest.main()
