from __future__ import annotations

from pathlib import Path
import re
import unittest

from repository.catalogue.schema import (
    COLUMN_COMMENTS,
    TABLE_COMMENTS,
    TABLE_LAYOUTS,
    expected_columns,
)


_LAKE_SCHEMA = Path(__file__).resolve().parents[2] / "LAKE_SCHEMA.md"


class LakeSchemaContractTests(unittest.TestCase):
    def test_physical_contract_has_no_urls_table(self) -> None:
        self.assertNotIn("urls", expected_columns())
        self.assertEqual(
            set(expected_columns()),
            {
                "artifacts",
                "crawl_attempts",
                "crawl_steps",
                "crawls",
                "documents",
                "elements",
            },
        )

    def test_document_and_element_buckets_remain_aligned(self) -> None:
        self.assertEqual(
            TABLE_LAYOUTS["documents"].partition_by,
            ("bucket(64, document_id)",),
        )
        self.assertEqual(
            TABLE_LAYOUTS["elements"].partition_by,
            TABLE_LAYOUTS["documents"].partition_by,
        )

    def test_installed_comments_match_the_canonical_document_exactly(self) -> None:
        source = _LAKE_SCHEMA.read_text(encoding="utf-8")
        documented_tables = dict(
            re.findall(
                r"COMMENT ON TABLE (\w+) IS\s*\n\s*'([^']*)';",
                source,
            )
        )
        documented_columns = {
            (table_name, column_name): comment
            for table_name, column_name, comment in re.findall(
                r"COMMENT ON COLUMN (\w+)\.(\w+) IS\s*\n\s*'([^']*)';",
                source,
            )
        }

        self.assertEqual(documented_tables, TABLE_COMMENTS)
        self.assertEqual(
            documented_columns,
            {
                (table_name, column_name): comment
                for table_name, comments in COLUMN_COMMENTS.items()
                for column_name, comment in comments.items()
            },
        )
        self.assertEqual(
            len(documented_columns),
            sum(len(columns) for columns in expected_columns().values()),
        )


if __name__ == "__main__":
    unittest.main()
