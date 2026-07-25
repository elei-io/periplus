from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

from sqlglot import exp, parse_one


PACK_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACK_ROOT.parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from benchmarks.analytical_ground_truth.claim_lineage import (
    SCENARIO_VERSION,
    expected_counts,
    expected_rows,
    manifest,
    observations,
    pages,
)


class ClaimLineagePackTests(unittest.TestCase):
    def test_manifest_freezes_three_claim_controls(self) -> None:
        source = observations()
        config = manifest()

        self.assertEqual(config["claim_count"], 3)
        self.assertEqual(config["page_count"], 30)
        self.assertEqual(config["crawl_count"], 90)
        self.assertEqual(len(source), 90)
        self.assertEqual(len({item.document_id for item in source}), 30)
        self.assertEqual(len({item.page.domain for item in source}), 30)

    def test_pages_are_deterministic_and_reused_across_observations(self) -> None:
        self.assertEqual(observations(), observations())
        history = [
            item
            for item in observations()
            if item.page.index == 0
        ]
        self.assertEqual(len(history), 3)
        self.assertEqual(
            {item.document_id for item in history},
            {history[0].document_id},
        )
        self.assertEqual(expected_counts()["documents"], 30)
        self.assertGreater(expected_counts()["elements"], 30)

    def test_html_contains_public_evidence_but_not_truth_labels(self) -> None:
        forbidden = (
            SCENARIO_VERSION,
            "claim_key",
            "independent_origin",
            "deep_copy_tree",
            "separate_roots",
            "single_weak_root",
        )
        for page in pages():
            self.assertIn(page.publisher, page.html)
            self.assertIn(f"Station {page.claim.station}", page.html)
            self.assertIn(f"{page.claim.value_ppm} ppm", page.html)
            for label in forbidden:
                self.assertNotIn(label, page.html)

    def test_oracle_proves_domain_count_is_not_independence(self) -> None:
        fixture = json.loads(
            (PACK_ROOT / "expected" / "claim_lineage.json").read_text(
                encoding="utf-8"
            )
        )
        rows = expected_rows()

        self.assertEqual(
            [row["claim_key"] for row in rows],
            fixture["expected_order"],
        )
        independent, copied, weak = rows
        self.assertEqual(
            (
                independent["occurrence_count"],
                independent["independent_origin_count"],
            ),
            (8, 8),
        )
        self.assertEqual(
            (
                copied["occurrence_count"],
                copied["domain_count"],
                copied["independent_origin_count"],
                copied["maximum_citation_depth"],
            ),
            (12, 12, 1, 4),
        )
        self.assertEqual(
            (
                weak["occurrence_count"],
                weak["independent_origin_count"],
            ),
            (10, 1),
        )
        self.assertGreater(
            copied["domain_count"],
            independent["domain_count"],
        )
        self.assertLess(
            copied["independent_origin_count"],
            independent["independent_origin_count"],
        )

    def test_query_uses_only_raw_crawls_and_elements(self) -> None:
        sql = (PACK_ROOT / "queries" / "claim_lineage.sql").read_text(
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


if __name__ == "__main__":
    unittest.main()
