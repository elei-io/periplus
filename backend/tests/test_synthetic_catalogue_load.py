from __future__ import annotations

from datetime import date
import unittest

import duckdb

from repository.catalogue.schema import expected_columns
from repository.catalogue.synthetic_load import (
    SyntheticBatch,
    SyntheticLoadConfig,
    batch_insert_sql,
    expected_counts,
    is_batch_capacity_pressure,
    synthetic_batches,
)


class SyntheticCatalogueLoadTests(unittest.TestCase):
    def test_configuration_has_expected_full_scale_counts(self) -> None:
        config = SyntheticLoadConfig(start_date=date(2026, 5, 26))

        counts = expected_counts(config)

        self.assertEqual(config.total_crawls, 1_500_000)
        self.assertEqual(config.batch_count, 3_000)
        self.assertEqual(counts["crawls"], 1_500_000)
        self.assertEqual(counts["documents"], 1_365_000)
        self.assertEqual(counts["artifacts"], 15_000)
        self.assertGreater(counts["elements"], 13_000_000_000)
        self.assertLess(counts["elements"], 14_500_000_000)

    def test_batches_cover_each_crawl_exactly_once(self) -> None:
        config = SyntheticLoadConfig(
            start_date=date(2026, 5, 26),
            days=2,
            crawls_per_day=1_000,
            batch_size=500,
        )

        batches = tuple(synthetic_batches(config))

        self.assertEqual(
            batches,
            (
                SyntheticBatch(index=0, start=0, stop=500),
                SyntheticBatch(index=1, start=500, stop=1_000),
                SyntheticBatch(index=2, start=1_000, stop=1_500),
                SyntheticBatch(index=3, start=1_500, stop=2_000),
            ),
        )

    def test_generated_batch_matches_schema_and_expected_counts(self) -> None:
        config = SyntheticLoadConfig(
            start_date=date(2026, 5, 26),
            days=1,
            crawls_per_day=100,
            batch_size=100,
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        for table, columns in expected_columns().items():
            definitions = ", ".join(
                f'"{name}" {_data_type(column.data_type)}'
                + ("" if column.nullable else " NOT NULL")
                for name, column in columns.items()
            )
            connection.execute(f'CREATE TABLE main."{table}" ({definitions})')

        for _table, sql in batch_insert_sql(
            config,
            SyntheticBatch(index=0, start=0, stop=100),
        ):
            connection.execute(sql)

        actual = {
            table: connection.execute(
                f'SELECT count(*) FROM main."{table}"'
            ).fetchone()[0]
            for table in expected_columns()
        }
        self.assertEqual(actual, expected_counts(config))
        duplicate_documents = connection.execute(
            """
            SELECT count(*) - count(DISTINCT document_id)
            FROM main.crawls
            WHERE document_id IS NOT NULL
            """
        ).fetchone()[0]
        self.assertEqual(duplicate_documents, 5)
        invalid_elements = connection.execute(
            """
            SELECT count(*)
            FROM main.elements AS element
            JOIN main.documents AS document USING (document_id)
            WHERE element.element_index >= document.element_count
               OR element.subtree_end_index < element.element_index
            """
        ).fetchone()[0]
        self.assertEqual(invalid_elements, 0)

    def test_capacity_pressure_is_narrowly_classified(self) -> None:
        self.assertTrue(
            is_batch_capacity_pressure(
                duckdb.OutOfMemoryException(
                    "could not allocate block of size 64 MiB"
                )
            )
        )
        self.assertTrue(
            is_batch_capacity_pressure(
                duckdb.OutOfMemoryException(
                    "failed to pin block of size 256 KiB"
                )
            )
        )
        self.assertFalse(
            is_batch_capacity_pressure(
                duckdb.InvalidInputException("syntax error")
            )
        )


def _data_type(value: object) -> str:
    renderer = getattr(value, "sql", None)
    return str(renderer() if callable(renderer) else value)


if __name__ == "__main__":
    unittest.main()
