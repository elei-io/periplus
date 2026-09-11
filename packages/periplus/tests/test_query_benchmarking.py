from __future__ import annotations

from importlib.resources import files

import json
import runpy
import sys
import io
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import duckdb
from dataclasses import replace
from unittest.mock import MagicMock, call, patch

from periplus.query.benchmarking import compare_reports, discover_cases, inspect_profile, result_digest, bounded_rows, deadline, measure_pair, BenchmarkFailure, MeasurementProgress, _measure


class QueryBenchmarkingTests(unittest.TestCase):
    def test_bounded_results_never_accept_a_partial_result(self):
        connection = duckdb.connect()
        try:
            with self.assertRaises(ValueError):
                bounded_rows(connection.execute("SELECT * FROM range(3)"), max_rows=2)
            with self.assertRaises(ValueError):
                bounded_rows(connection.execute("SELECT repeat('x', 100)"), max_bytes=10)
        finally:
            connection.close()

    def test_deadline_interrupts_native_execution(self):
        connection = duckdb.connect()
        try:
            with self.assertRaises(duckdb.InterruptException):
                with deadline(connection, .02):
                    connection.execute("SELECT sum(i::HUGEINT) FROM range(100000000000) r(i)")
            self.assertEqual(connection.execute("SELECT 1").fetchone(), (1,))
        finally:
            connection.close()

    def test_checked_in_cases_bind_to_current_public_contract(self):
        root = Path(__file__).resolve().parents[3] / "benchmarks/query/cases"
        connection = duckdb.connect()
        try:
            connection.execute("CREATE SCHEMA public_v1")
            connection.execute("CREATE TABLE public_v1.capture(capture_id UUID, requested_url VARCHAR, effective_url VARCHAR, captured_at TIMESTAMPTZ, http_status_code INTEGER, content_id VARCHAR)")
            connection.execute("CREATE TABLE public_v1.html_element(content_id VARCHAR, node_index INTEGER, tag VARCHAR, attributes MAP(VARCHAR,VARCHAR), parent_index INTEGER, sibling_index INTEGER, subtree_end_index INTEGER, text_direct VARCHAR)")
            connection.execute("CREATE TABLE public_v1.link(capture_id UUID, node_index INTEGER, raw_href VARCHAR, resolved_url VARCHAR)")
            connection.execute("CREATE TABLE public_v1.prose(content_id VARCHAR, text VARCHAR)")
            connection.execute("CREATE TABLE public_v1.term(content_id VARCHAR,text VARCHAR,frequency BIGINT)")
            connection.execute("CREATE TABLE public_v1.term_node(content_id VARCHAR, text VARCHAR, node_index INTEGER, frequency BIGINT)")
            connection.execute("CREATE TABLE public_v1.html_heading(content_id VARCHAR, node_index INTEGER, text VARCHAR)")
            connection.execute("CREATE TABLE public_v1.html_section(content_id VARCHAR, heading_node_index INTEGER)")
            connection.execute("CREATE TABLE public_v1.html_jsonld(content_id VARCHAR, node_index INTEGER, value JSON, parse_error VARCHAR)")
            connection.execute("CREATE TABLE public_v1.html_metadata(content_id VARCHAR, node_index INTEGER, kind VARCHAR, name VARCHAR, value VARCHAR)")
            connection.execute("CREATE TABLE public_v1.html_node(content_id VARCHAR, node_index INTEGER, subtree_end_index INTEGER, node_type VARCHAR, value VARCHAR, parent_index INTEGER)")
            connection.execute(files("periplus.platform.catalogue").joinpath("sql/public_v1/helpers/subtree_text.sql").read_text())
            for case in discover_cases(root).values():
                parameters = {"scope": case.scales[0]} if case.scales[0] else None
                connection.execute("EXPLAIN " + case.sql, parameters)
        finally:
            connection.close()

    def test_pair_keeps_both_variants_in_one_transaction(self):
        root = Path(__file__).resolve().parents[3] / "benchmarks/query/cases"
        case = discover_cases(root)["exact-page-history"]
        candidate = replace(case, title="candidate")
        connection = MagicMock()
        seen = []
        def measure(conn, item, scale, runs, *, profile_warm_runs):
            self.assertTrue(profile_warm_runs)
            self.assertIs(conn, connection)
            seen.append(item.title)
            return item
        with patch("periplus.query.benchmarking._connection", return_value=connection), patch("periplus.query.benchmarking._measure", side_effect=measure):
            result = measure_pair(case, candidate, None, warm_runs=1, candidate_first=True)
        self.assertEqual(seen, ["candidate", case.title])
        self.assertEqual(list(result), ["candidate", "baseline"])
        self.assertEqual(connection.execute.call_args_list, [call("BEGIN TRANSACTION"), call("ROLLBACK")])
        connection.close.assert_called_once()

    def test_pair_rejects_incompatible_catalogue_before_execution(self):
        root = Path(__file__).resolve().parents[3] / "benchmarks/query/cases"
        case = discover_cases(root)["gov-heading-sections"]
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = []
        scope = MagicMock()
        scope.matches.return_value = False
        with patch("periplus.query.benchmarking._connection", return_value=connection), patch("periplus.query.benchmarking.catalogue_config_from_env", return_value=MagicMock(alias="periplus")), patch("periplus.query.benchmarking._measure") as measure:
            with self.assertRaises(ValueError):
                measure_pair(case, case, None, warm_runs=1, verify_scope=scope)
            measure.assert_not_called()
        connection.close.assert_called_once()
        self.assertEqual(connection.execute.call_args, call("ROLLBACK"))

    def test_content_scope_cli_requires_speedup_as_well_as_equal_results(self):
        script = Path(__file__).resolve().parents[1] / "scripts/query_benchmark.py"
        main = runpy.run_path(str(script))["main"]
        baseline = dict(case="gov-heading-sections", scale=None, ducklake_snapshot=42,
                        columns=["x"], types=["INTEGER"], result_rows=1,
                        result_digest="same", median_warm_ms=20, normal_ms=20,
                        peak_buffer_bytes=[100], cumulative_rows_scanned=[10],
                        within_time_budget=True)
        for timing, succeeds in [(10, True), (30, False)]:
            with TemporaryDirectory() as directory:
                report = Path(directory) / "report.json"
                pair = {"baseline": baseline, "candidate": {**baseline, "median_warm_ms": timing}}
                argv = [str(script), "--case", "gov-heading-sections", "--content-scope", "--report", str(report)]
                with patch.object(sys, "argv", argv), patch.dict(main.__globals__, {"measure_pair": lambda *a, **kw: pair, "environment_metadata": lambda: {}}), redirect_stdout(io.StringIO()):
                    if succeeds:
                        main()
                    else:
                        with self.assertRaises(SystemExit):
                            main()
                payload = json.loads(report.read_text())
                self.assertTrue(payload["comparison"][0]["exact_result"])
                self.assertEqual(payload["comparison"][0]["performance_improved"], succeeds)
                self.assertEqual(bool(payload["failures"]), not succeeds)

    def test_single_execution_records_no_warm_timing(self):
        root = Path(__file__).resolve().parents[3] / "benchmarks/query/cases"
        case = discover_cases(root)["exact-page-history"]
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = (42,)
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(7,), None]
        cursor.description = [("n", "INTEGER")]
        with patch("periplus.query.benchmarking.catalogue_config_from_env", return_value=MagicMock(alias="periplus")), patch("periplus.query.benchmarking._execute", return_value=cursor) as execute:
            measured = _measure(connection, case, None, 0)
        self.assertIsNone(measured.median_warm_ms)
        self.assertEqual(measured.warm_ms, ())
        self.assertEqual(measured.result_rows, 1)
        execute.assert_called_once()

    def test_ordinary_warm_execution_has_no_fabricated_scan_metrics(self):
        root = Path(__file__).resolve().parents[3] / "benchmarks/query/cases"
        case = discover_cases(root)["exact-page-history"]
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = (42,)
        cursors = []
        for _ in range(2):
            cursor = MagicMock()
            cursor.fetchone.side_effect = [(7,), None]
            cursor.description = [("n", "INTEGER")]
            cursors.append(cursor)
        with patch("periplus.query.benchmarking.catalogue_config_from_env", return_value=MagicMock(alias="periplus")), patch("periplus.query.benchmarking._execute", side_effect=cursors):
            measured = _measure(connection, case, None, 1, profile_warm_runs=False)
        self.assertEqual(measured.result_rows, 1)
        self.assertEqual(len(measured.warm_ms), 1)
        self.assertEqual(measured.cumulative_rows_scanned, ())
        self.assertEqual(measured.scans, ())

    def test_ordinary_warm_execution_rejects_changed_results(self):
        root = Path(__file__).resolve().parents[3] / "benchmarks/query/cases"
        case = discover_cases(root)["exact-page-history"]
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = (42,)
        cursors = []
        for value in (7, 8):
            cursor = MagicMock()
            cursor.fetchone.side_effect = [(value,), None]
            cursor.description = [("n", "INTEGER")]
            cursors.append(cursor)
        with patch("periplus.query.benchmarking.catalogue_config_from_env", return_value=MagicMock(alias="periplus")), patch("periplus.query.benchmarking._execute", side_effect=cursors) as execute:
            with self.assertRaises(BenchmarkFailure) as caught:
                _measure(connection, case, None, 1, profile_warm_runs=False)
        self.assertEqual(caught.exception.error_type, "ValueError")
        self.assertTrue(all(call.args[1] == case.sql for call in execute.call_args_list))

    def test_interrupted_profile_retains_safe_normal_execution_evidence(self):
        root = Path(__file__).resolve().parents[3] / "benchmarks/query/cases"
        case = discover_cases(root)["exact-page-history"]
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = (42,)
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(7,), None]
        cursor.description = [("n", "INTEGER")]
        with patch("periplus.query.benchmarking.catalogue_config_from_env", return_value=MagicMock(alias="periplus")), patch("periplus.query.benchmarking._execute", side_effect=[cursor, duckdb.InterruptException("secret-connection-string")]):
            with self.assertRaises(BenchmarkFailure) as caught:
                _measure(connection, case, None, 1)
        failure = caught.exception
        self.assertEqual(failure.error_type, "InterruptException")
        self.assertEqual(failure.progress["snapshot"], 42)
        self.assertEqual(failure.progress["phase"], "warm_profile")
        self.assertEqual(failure.progress["result_rows"], 1)
        self.assertIsNotNone(failure.progress["normal_ms"])
        self.assertNotIn("secret", str(failure))
        self.assertNotIn("secret", json.dumps(failure.progress))

    def test_failed_candidate_preserves_complete_baseline_without_comparison(self):
        root = Path(__file__).resolve().parents[3] / "benchmarks/query/cases"
        case = discover_cases(root)["exact-page-history"]
        failure = BenchmarkFailure("InterruptException", MeasurementProgress(case=case.identifier, scale=None))
        connection = MagicMock()
        with patch("periplus.query.benchmarking._connection", return_value=connection), patch("periplus.query.benchmarking._measure", side_effect=[case, failure]):
            with self.assertRaises(BenchmarkFailure) as caught:
                measure_pair(case, case, None, warm_runs=1)
        self.assertEqual(caught.exception.variant, "candidate")
        self.assertEqual(list(caught.exception.completed_variants), ["baseline"])
        connection.close.assert_called_once()

    def test_discovers_a_parameterized_public_sql_case(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = root / "example"
            case.mkdir()
            (case / "case.toml").write_text(
                """
id = "example"
title = "Example"
use_case = "A person asks a real question."
classification = "optimizer"
ordered = true
scales = [10, 20]
memory_limit = "512MB"
max_warm_ms = 60000
""",
                encoding="utf-8",
            )
            (case / "query.sql").write_text(
                "SELECT * FROM public_v1.capture LIMIT $scope", encoding="utf-8"
            )

            loaded = discover_cases(root)["example"]

            self.assertEqual(loaded.scales, (10, 20))
            self.assertTrue(loaded.ordered)

    def test_result_digest_distinguishes_sequence_from_bag(self) -> None:
        left = [(1,), (2,)]
        right = [(2,), (1,)]
        self.assertEqual(
            result_digest(left, ordered=False), result_digest(right, ordered=False)
        )
        self.assertNotEqual(
            result_digest(left, ordered=True), result_digest(right, ordered=True)
        )

    def test_profile_records_all_inputs_around_blocking_operators(self) -> None:
        profile = {
            "operator_name": "HASH_JOIN",
            "operator_cardinality": 3,
            "operator_rows_scanned": 0,
            "children": [
                {
                    "operator_name": "DUCKLAKE_SCAN",
                    "operator_cardinality": 10,
                    "operator_rows_scanned": 100,
                    "extra_info": {"Table": "left", "Total Files Read": "2"},
                    "children": [],
                },
                {
                    "operator_name": "TABLE_SCAN",
                    "operator_cardinality": 4,
                    "operator_rows_scanned": 4,
                    "extra_info": {"Table": "right"},
                    "children": [],
                },
            ],
        }

        blocking, scans = inspect_profile(profile)

        self.assertEqual(blocking[0]["input_cardinalities"], [10, 4])
        self.assertEqual(blocking[0]["output_cardinality"], 3)
        self.assertEqual([scan["table"] for scan in scans], ["left", "right"])

    def test_report_comparison_enforces_exact_results(self) -> None:
        measurement = {
            "case": "example",
            "scale": 10,
            "ducklake_snapshot": 42,
            "columns": ["value"],
            "types": ["INTEGER"],
            "result_rows": 1,
            "result_digest": "same",
            "median_warm_ms": 20, "normal_ms": 20,
            "peak_buffer_bytes": [200],
            "cumulative_rows_scanned": [100],
        }
        baseline = {"measurements": [measurement]}
        candidate = json.loads(json.dumps(baseline))
        candidate["measurements"][0]["columns"] = ("value",)
        candidate["measurements"][0]["types"] = ("INTEGER",)

        comparisons, failures = compare_reports(baseline, candidate)

        self.assertTrue(comparisons[0]["exact_result"])
        self.assertEqual(failures, [])

        candidate["measurements"][0]["result_digest"] = "different"

        comparisons, failures = compare_reports(baseline, candidate)

        self.assertFalse(comparisons[0]["exact_result"])
        self.assertEqual(len(failures), 1)

    def test_report_comparison_rejects_different_snapshots(self) -> None:
        measurement = {
            "case": "example",
            "scale": None,
            "ducklake_snapshot": 42,
            "columns": ["value"],
            "types": ["INTEGER"],
            "result_rows": 1,
            "result_digest": "same",
            "median_warm_ms": 20, "normal_ms": 20,
            "peak_buffer_bytes": [200],
            "cumulative_rows_scanned": [100],
        }
        candidate = {"measurements": [{**measurement, "ducklake_snapshot": 43}]}

        comparisons, failures = compare_reports(
            {"measurements": [measurement]}, candidate
        )

        self.assertFalse(comparisons[0]["exact_result"])
        self.assertIn("snapshot mismatch", failures[0])


if __name__ == "__main__":
    unittest.main()
