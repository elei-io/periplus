from __future__ import annotations

import unittest
import json

import httpx

from atlas_sql import (
    AtlasCompiler,
    AtlasCompilerUnavailable,
    CompilationOutcome,
    InteractiveQueryPurpose,
    TableMacroDefinition,
)


class AtlasSqlEmbeddedCompilerTests(unittest.TestCase):
    def test_embedded_facade_preserves_non_throwing_contract(self) -> None:
        compiler = AtlasCompiler.embedded(catalogue_revision="revision-7")

        invalid = compiler.compile("SELECT FROM documents")
        unsupported = compiler.compile(
            "SELECT macros.missing(document_id) FROM documents"
        )
        unchanged = compiler.compile(
            "SELECT document_id FROM documents LIMIT 10"
        )

        self.assertEqual(invalid.outcome, CompilationOutcome.INVALID)
        self.assertIsNone(invalid.executable_sql)
        self.assertEqual(
            unsupported.outcome,
            CompilationOutcome.UNSUPPORTED,
        )
        self.assertEqual(
            unsupported.executable_sql,
            unsupported.authored_sql,
        )
        self.assertEqual(unchanged.outcome, CompilationOutcome.UNCHANGED)
        self.assertEqual(unchanged.catalogue_revision, "revision-7")
        self.assertTrue(unchanged.compiler_version)

    def test_embedded_facade_accepts_authoritative_definitions(self) -> None:
        result = AtlasCompiler.embedded().compile(
            "SELECT * FROM macros.suggest_records('hello')",
            purpose=InteractiveQueryPurpose(
                table_macros=(
                    TableMacroDefinition(
                        schema_name="macros",
                        macro_name="suggest_records",
                        parameters=("value",),
                        parameter_defaults=(),
                        sql="SELECT value AS result",
                    ),
                )
            ),
        )

        self.assertTrue(result.supported)
        self.assertEqual(result.outcome, CompilationOutcome.OPTIMIZED)
        self.assertNotIn("suggest_records", result.executable_sql or "")

    def test_explain_is_valid_interactive_sql(self) -> None:
        result = AtlasCompiler.embedded().compile(
            "EXPLAIN SELECT * FROM documents"
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.outcome, CompilationOutcome.UNCHANGED)
        self.assertEqual(
            result.executable_sql,
            "EXPLAIN SELECT * FROM documents",
        )


class AtlasSqlRemoteCompilerTests(unittest.TestCase):
    def test_remote_facade_uses_public_compile_contract(self) -> None:
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "purpose": "interactive",
                    "outcome": "optimized",
                    "authored_sql": "SELECT 1",
                    "executable_sql": "SELECT 1",
                    "diagnostics": [],
                    "applied_rewrites": [],
                    "catalogue_revision": "lake-42",
                    "compiler_version": "1.2.3",
                    "valid": True,
                    "supported": True,
                    "materialization_eligible": False,
                },
            )

        backend = AtlasCompiler.remote("https://atlas.example.com", token="secret")
        backend._backend._client = httpx.Client(  # type: ignore[attr-defined]
            base_url="https://atlas.example.com/",
            transport=httpx.MockTransport(handle),
            headers={"Authorization": "Bearer secret"},
        )

        result = backend.compile("SELECT 1")

        self.assertEqual(result.catalogue_revision, "lake-42")
        self.assertEqual(
            requests[0].url.path,
            "/catalogue/sql/compile",
        )
        self.assertEqual(
            requests[0].headers["authorization"],
            "Bearer secret",
        )

    def test_remote_failure_does_not_claim_sql_is_valid(self) -> None:
        def handle(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"detail": "unavailable"})

        compiler = AtlasCompiler.remote("https://atlas.example.com")
        compiler._backend._client = httpx.Client(  # type: ignore[attr-defined]
            base_url="https://atlas.example.com/",
            transport=httpx.MockTransport(handle),
        )

        with self.assertRaises(AtlasCompilerUnavailable):
            compiler.compile("SELECT 1")

    def test_remote_facade_returns_comparative_lake_analysis(self) -> None:
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "compilation": {
                        "purpose": "interactive",
                        "outcome": "optimized",
                        "authored_sql": "SELECT 1",
                        "executable_sql": "SELECT 1",
                        "diagnostics": [],
                        "applied_rewrites": [],
                        "catalogue_revision": "lake-42",
                        "compiler_version": "1.2.3",
                        "valid": True,
                        "supported": True,
                        "materialization_eligible": False,
                    },
                    "authored": {
                        "status": "succeeded",
                        "metrics": {
                            "latency_seconds": 2.0,
                            "cpu_seconds": 1.5,
                            "total_bytes_read": 1000,
                            "peak_buffer_memory_bytes": 500,
                            "cumulative_rows_scanned": 100,
                            "cumulative_cardinality": 10,
                        },
                    },
                    "compiled": {
                        "status": "succeeded",
                        "metrics": {
                            "latency_seconds": 0.5,
                            "cpu_seconds": 0.4,
                            "total_bytes_read": 100,
                            "peak_buffer_memory_bytes": 50,
                            "cumulative_rows_scanned": 10,
                            "cumulative_cardinality": 10,
                        },
                    },
                    "comparison": {
                        "latency_speedup": 4.0,
                        "cpu_speedup": 3.75,
                        "rows_scanned_reduction": 0.9,
                        "bytes_read_reduction": 0.9,
                        "peak_buffer_memory_reduction": 0.9,
                    },
                    "execution_policy": "cold",
                    "per_plan_budget_seconds": 60.0,
                },
            )

        compiler = AtlasCompiler.remote("https://atlas.example.com")
        compiler._backend._client = httpx.Client(  # type: ignore[attr-defined]
            base_url="https://atlas.example.com/",
            transport=httpx.MockTransport(handle),
        )

        result = compiler.analyze("SELECT 1")

        self.assertEqual(result.comparison.latency_speedup, 4.0)
        self.assertEqual(
            result.compiled.metrics.cumulative_rows_scanned,  # type: ignore[union-attr]
            10,
        )
        self.assertEqual(
            requests[0].url.path,
            "/catalogue/sql/analyze",
        )
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["per_plan_budget_seconds"], 60.0)
        self.assertEqual(payload["execution_policy"], "cold")
        self.assertEqual(payload["warmup_runs"], 1)
        self.assertFalse(payload["equivalence_hashes"])


if __name__ == "__main__":
    unittest.main()
