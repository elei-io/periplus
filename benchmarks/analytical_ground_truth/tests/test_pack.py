from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
import unittest

from sqlglot import exp, parse_one


PACK_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACK_ROOT.parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from benchmarks.analytical_ground_truth import cli
from benchmarks.analytical_ground_truth.pack import (
    PACK_NAME,
    expected_counts,
    expected_rows,
    manifest,
    observations,
    retailer_indexes,
)


class ProductMarketPackTests(unittest.TestCase):
    def test_manifest_and_generator_freeze_the_first_slice(self) -> None:
        source = observations()
        config = manifest()

        self.assertEqual(len(source), 960)
        self.assertEqual(len(source), config["crawl_count"])
        self.assertLessEqual(len(source), 1000)
        self.assertEqual(len({item.product.gtin for item in source}), 24)
        self.assertEqual(len({item.retailer_domain for item in source}), 12)
        counts = Counter(item.product.gtin for item in source)
        self.assertEqual(set(counts.values()), {40})

    def test_each_product_has_four_deterministic_independent_retailers(self) -> None:
        source = observations()
        actual = defaultdict(set)
        for item in source:
            actual[item.product.index].add(item.retailer_index)

        self.assertEqual(
            {
                product_index: tuple(sorted(values))
                for product_index, values in actual.items()
            },
            {
                product_index: retailer_indexes(product_index)
                for product_index in range(24)
            },
        )

    def test_generation_is_deterministic_and_reuses_unchanged_documents(self) -> None:
        first = observations()
        second = observations()

        self.assertEqual(first, second)
        self.assertLess(
            expected_counts(first)["documents"],
            expected_counts(first)["crawls"] * 0.6,
        )
        self.assertGreater(
            expected_counts(first)["elements"],
            expected_counts(first)["documents"],
        )
        history = [
            item
            for item in first
            if item.product.index == 1 and item.retailer_index == 1
        ]
        self.assertEqual(history[0].document_id, history[1].document_id)
        self.assertEqual(history[1].document_id, history[2].document_id)
        self.assertNotEqual(history[2].document_id, history[3].document_id)

    def test_generated_html_does_not_leak_truth_labels(self) -> None:
        forbidden = (
            PACK_NAME,
            "canonical_product",
            "near_match",
            "expected_rank",
            "final_state",
        )
        for item in observations():
            if item.html is None:
                continue
            for label in forbidden:
                self.assertNotIn(label, item.html)

    def test_truth_oracle_has_exact_rows_and_negative_controls(self) -> None:
        expected_fixture = json.loads(
            (PACK_ROOT / "expected" / "product_market.json").read_text(
                encoding="utf-8"
            )
        )
        rows = expected_rows()

        self.assertEqual(len(rows), expected_fixture["row_count"])
        self.assertEqual(
            [row["gtin"] for row in rows],
            expected_fixture["expected_gtins"],
        )
        self.assertEqual({row["retailer_count"] for row in rows}, {4})
        self.assertGreater(sum(row["removed_count"] for row in rows), 0)
        self.assertGreater(sum(row["unknown_count"] for row in rows), 0)
        for left, right in expected_fixture["near_match_pairs"]:
            self.assertIn(left, {row["gtin"] for row in rows})
            self.assertIn(right, {row["gtin"] for row in rows})
            self.assertNotEqual(left, right)

    def test_authored_query_reads_only_canonical_raw_tables(self) -> None:
        sql = (PACK_ROOT / "queries" / "product_market.sql").read_text(
            encoding="utf-8"
        )
        query = parse_one(sql, dialect="duckdb")
        ctes = {
            value.alias_or_name.lower()
            for value in query.find_all(exp.CTE)
        }
        relations = {
            value.name.lower()
            for value in query.find_all(exp.Table)
            if value.name.lower() not in ctes
        }

        self.assertEqual(relations, {"crawls", "elements"})
        self.assertNotIn("views.", sql.lower())
        self.assertNotIn("macros.", sql.lower())
        self.assertNotIn("materialized", sql.lower())

    def test_lake_safety_refuses_production_and_requires_overlay_flag(self) -> None:
        with self.assertRaises(SystemExit):
            cli._validate_lake("atlas", allow_load_lake=True)
        with self.assertRaises(SystemExit):
            cli._validate_lake("atlas_load", allow_load_lake=False)
        cli._validate_lake("atlas_test", allow_load_lake=False)
        cli._validate_lake("atlas_load", allow_load_lake=True)


if __name__ == "__main__":
    unittest.main()
