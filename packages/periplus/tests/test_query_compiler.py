"""Compiler boundaries: admission before passes, explicit decisions and no chaining."""

import unittest
from unittest.mock import Mock

import duckdb
from sqlglot import exp

from element_fixture import catalogue
from periplus.query.compiler import compile_query
from periplus.query.models import QueryRequest, QueryMode
from periplus.query.optimizations import passes_for_mode
from periplus.query.optimizations.base import OptimizationPass, PassDecision


class QueryCompilerTests(unittest.TestCase):
    def setUp(self):
        self.catalogue = catalogue()
        self.db = self.catalogue.connection
        self.db.execute("SET schema='public_v1'")
        self.addCleanup(self.db.close)

    def compile(
        self, sql="SELECT 42 AS value", *, passes=(), execute=True, parameters=None
    ):
        return compile_query(
            self.db,
            QueryRequest(sql=sql, parameters=parameters or []),
            schema="public_v1",
            catalogue_alias="memory",
            execute=execute,
            max_rows=10,
            passes=passes,
        )

    def test_minimal_modes(self):
        self.assertEqual(passes_for_mode(QueryMode.STABLE), ())
        self.assertEqual(
            [p.name for p in passes_for_mode(QueryMode.EXPERIMENTAL)],
            ["element_text_index_candidates", "capture_link_scope"],
        )
        native = self.compile()
        self.assertEqual(native.optimizations, [])
        self.assertEqual(native.diagnostics, [])
        self.assertEqual(self.db.execute(native.executable_sql).fetchall(), [(42,)])

    def test_public_validation_and_native_binding_precede_passes(self):
        run = Mock(side_effect=AssertionError("pass reached invalid input"))
        passes = (OptimizationPass("never", run),)
        for sql in (
            "SELECT * FROM material.html_terms",
            "DELETE FROM capture",
            "SELECT * FROM experimental.page",
        ):
            with self.assertRaises(ValueError):
                self.compile(sql, passes=passes)
        with self.assertRaises(duckdb.BinderException):
            self.compile("SELECT missing FROM page", passes=passes)
        run.assert_not_called()

    def test_ordered_alternatives_do_not_chain_or_mutate_original(self):
        seen = []

        def decline(context):
            seen.append("decline")
            context.statement.set("limit", exp.Limit(expression=exp.Literal.number(0)))
            return PassDecision(
                "budget_exceeded",
                "contents",
                "Candidate budget exceeded.",
                counts={"candidate_contents": 129},
            )

        def apply(context):
            seen.append("apply")
            self.assertIsNone(context.statement.args.get("limit"))
            return PassDecision(
                "applied", "identity", "Preserved query.", statement=context.statement
            )

        never = Mock(side_effect=AssertionError("passes chained"))
        compiled = self.compile(
            passes=(
                OptimizationPass("bounded", decline),
                OptimizationPass("identity", apply),
                OptimizationPass("never", never),
            )
        )
        self.assertEqual(seen, ["decline", "apply"])
        self.assertEqual(compiled.optimizations, ["identity"])
        self.assertEqual(
            [d.code for d in compiled.diagnostics],
            ["bounded.budget_exceeded.contents", "identity.applied.identity"],
        )
        self.assertIn("candidate_contents=129", compiled.diagnostics[0].message)
        self.assertEqual(self.db.execute(compiled.executable_sql).fetchall(), [(42,)])
        never.assert_not_called()

    def test_preparation_has_no_lookup_connection_and_stops_at_deferred(self):
        def deferred(context):
            self.assertIsNone(context.connection)
            return PassDecision(
                "deferred", "lookup", "Execution requires a bounded lookup."
            )

        never = Mock(side_effect=AssertionError("continued after deferred pass"))
        compiled = self.compile(
            execute=False,
            passes=(
                OptimizationPass("deferred", deferred),
                OptimizationPass("never", never),
            ),
        )
        self.assertEqual(compiled.optimizations, [])
        self.assertEqual(compiled.diagnostics[0].code, "deferred.deferred.lookup")
        never.assert_not_called()

    def test_inspection_never_runs_a_pass(self):
        run = Mock(side_effect=AssertionError("inspection ran pass"))
        passes = (OptimizationPass("never", run),)
        for sql in (
            "EXPLAIN SELECT 1",
            "EXPLAIN ANALYZE SELECT 1",
            "DESCRIBE page",
            "SUMMARIZE page",
            "SHOW TABLES FROM public_v1",
        ):
            self.compile(sql, passes=passes)
        run.assert_not_called()

    def test_unexpected_pass_failures_propagate(self):
        run = Mock(side_effect=RuntimeError("pass bug"))
        with self.assertRaisesRegex(RuntimeError, "pass bug"):
            self.compile(passes=(OptimizationPass("broken", run),))

    def test_decision_requires_a_rewrite_only_when_applied(self):
        with self.assertRaises(ValueError):
            PassDecision("applied", "missing", "Missing rewrite.")
        with self.assertRaises(ValueError):
            PassDecision(
                "not_applicable",
                "unexpected",
                "Unexpected rewrite.",
                statement=exp.select("1"),
            )
