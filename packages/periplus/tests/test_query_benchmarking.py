from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from periplus.query.benchmarking import compare_reports, discover_cases, inspect_profile, result_digest


class QueryBenchmarkingTests(unittest.TestCase):
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
                "SELECT * FROM web.observation LIMIT $scope", encoding="utf-8"
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
            "median_warm_ms": 20,
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
            "median_warm_ms": 20,
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
