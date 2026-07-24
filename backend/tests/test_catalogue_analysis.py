from __future__ import annotations

import json
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from atlas_sql import AnalysisPlanStatus, AtlasCompiler
from repository.catalogue.quack_runtime import CatalogueQueryExecutionError
from repository.catalogue.analysis import analyze_compilation
from repository.catalogue.interactive import BufferedCatalogueResult


class CatalogueAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_compares_authored_and_compiled_profiles(self) -> None:
        compilation = AtlasCompiler.embedded().compile(
            """
            SELECT document_id
            FROM documents
            WHERE document_id IN (
                SELECT document_id FROM elements LIMIT 10
            )
            LIMIT 10
            """
        )
        compilation = compilation.model_copy(
            update={
                "outcome": "optimized",
                "executable_sql": "SELECT document_id FROM documents LIMIT 10",
            }
        )
        profiles = [
            _profile(rows_scanned=100, latency=2.0),
            _profile(rows_scanned=10, latency=0.5),
        ]

        with patch(
            "repository.catalogue.analysis.execute_interactive_query",
            new=AsyncMock(side_effect=profiles),
        ) as execute:
            result = await analyze_compilation(
                Mock(),
                compilation,
            )

        self.assertEqual(execute.await_count, 2)
        authored_call, compiled_call = execute.await_args_list
        self.assertEqual(
            authored_call.args[1],
            f"EXPLAIN ANALYZE {compilation.authored_sql}",
        )
        self.assertEqual(
            authored_call.kwargs["compilation"].authored_sql,
            authored_call.args[1],
        )
        self.assertEqual(
            authored_call.kwargs["compilation"].executable_sql,
            f"EXPLAIN ANALYZE {compilation.authored_sql}",
        )
        self.assertEqual(
            compiled_call.kwargs["compilation"].authored_sql,
            compiled_call.args[1],
        )
        self.assertEqual(
            compiled_call.kwargs["compilation"].executable_sql,
            f"EXPLAIN ANALYZE {compilation.executable_sql}",
        )
        self.assertEqual(result.comparison.latency_speedup, 4.0)
        self.assertEqual(result.comparison.rows_scanned_reduction, 0.9)
        self.assertEqual(
            result.compiled.metrics.cumulative_rows_scanned,  # type: ignore[union-attr]
            10,
        )
        self.assertEqual(result.authored.status, AnalysisPlanStatus.SUCCEEDED)
        self.assertEqual(
            result.comparison.operator_differences[0].rows_scanned_delta,
            -90,
        )

    async def test_retains_compiled_success_when_authored_plan_fails(self) -> None:
        compilation = AtlasCompiler.embedded().compile("SELECT 1")
        compilation = compilation.model_copy(
            update={"outcome": "optimized", "executable_sql": "SELECT 2"}
        )
        with patch(
            "repository.catalogue.analysis.execute_interactive_query",
            new=AsyncMock(
                side_effect=[
                    CatalogueQueryExecutionError("out of memory"),
                    _profile(rows_scanned=1, latency=0.1),
                ]
            ),
        ):
            result = await analyze_compilation(Mock(), compilation)

        self.assertEqual(
            result.authored.status,
            AnalysisPlanStatus.MEMORY_EXHAUSTED,
        )
        self.assertEqual(result.compiled.status, AnalysisPlanStatus.SUCCEEDED)
        self.assertIsNone(result.comparison)

    async def test_client_cancellation_is_not_converted_to_plan_failure(self) -> None:
        compilation = AtlasCompiler.embedded().compile("SELECT 1")
        with patch(
            "repository.catalogue.analysis.execute_interactive_query",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await analyze_compilation(Mock(), compilation)

    async def test_per_plan_timeout_does_not_prevent_second_plan(self) -> None:
        compilation = AtlasCompiler.embedded().compile("SELECT 1")
        compilation = compilation.model_copy(
            update={"outcome": "optimized", "executable_sql": "SELECT 2"}
        )
        calls = 0

        async def execute(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.sleep(1)
            return _profile(rows_scanned=1, latency=0.1)

        with patch(
            "repository.catalogue.analysis.execute_interactive_query",
            new=execute,
        ):
            result = await analyze_compilation(
                Mock(),
                compilation,
                per_plan_budget_seconds=0.001,
            )

        self.assertEqual(result.authored.status, AnalysisPlanStatus.TIMED_OUT)
        self.assertEqual(result.compiled.status, AnalysisPlanStatus.SUCCEEDED)

    async def test_optional_equivalence_hashes_compare_bounded_results(self) -> None:
        compilation = AtlasCompiler.embedded().compile("SELECT 1 AS value")
        compilation = compilation.model_copy(
            update={"outcome": "optimized", "executable_sql": "SELECT 1 value"}
        )
        data = BufferedCatalogueResult(
            query_id=uuid4(),
            columns=["value"],
            column_types=["integer"],
            rows=[[1]],
        )
        with patch(
            "repository.catalogue.analysis.execute_interactive_query",
            new=AsyncMock(
                side_effect=[
                    _profile(rows_scanned=1, latency=0.1),
                    data,
                    _profile(rows_scanned=1, latency=0.1),
                    data,
                ]
            ),
        ):
            result = await analyze_compilation(
                Mock(),
                compilation,
                equivalence_hashes=True,
            )

        self.assertTrue(result.comparison.equivalence_hashes_match)
        self.assertIsNotNone(result.authored.equivalence_hash)


def _profile(
    *,
    rows_scanned: int,
    latency: float,
) -> BufferedCatalogueResult:
    return BufferedCatalogueResult(
        query_id=uuid4(),
        columns=["explain_key", "explain_value"],
        column_types=["string", "string"],
        rows=[
            [
                "analyzed_plan",
                json.dumps(
                    {
                        "latency": latency,
                        "cpu_time": latency / 2,
                        "total_bytes_read": rows_scanned * 10,
                        "system_peak_buffer_memory": rows_scanned * 5,
                        "cumulative_rows_scanned": rows_scanned,
                        "cumulative_cardinality": 10,
                        "children": [
                            {
                                "operator_name": "SEQ_SCAN",
                                "operator_type": "TABLE_SCAN",
                                "operator_timing": latency / 2,
                                "operator_cardinality": 10,
                                "operator_rows_scanned": rows_scanned,
                            }
                        ],
                    }
                ),
            ]
        ],
    )


if __name__ == "__main__":
    unittest.main()
