import unittest

from atlas.query.http import _bounded_query


class SqlConsoleValidationTests(unittest.TestCase):
    def test_accepts_read_only_physical_catalogue_query(self):
        bounded = _bounded_query(
            """
            WITH recent AS (
                SELECT visit_id, requested_url
                FROM ingest.visits
            )
            SELECT recent.requested_url, pages.hostname
            FROM recent
            JOIN material.pages AS pages
              ON pages.normalized_url = recent.requested_url
            """
        )

        self.assertIn("LIMIT 10001", bounded)
        self.assertIn("FROM ingest.visits", bounded)

    def test_rejects_mutation(self):
        with self.assertRaisesRegex(ValueError, "read-only query"):
            _bounded_query("DELETE FROM ingest.visits")

    def test_rejects_multiple_statements(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            _bounded_query(
                "SELECT * FROM ingest.visits; SELECT * FROM material.pages"
            )

    def test_requires_physical_schema_qualification(self):
        with self.assertRaisesRegex(ValueError, "qualified"):
            _bounded_query("SELECT * FROM visits")

    def test_rejects_non_public_schema(self):
        with self.assertRaisesRegex(ValueError, "ingest.* and material"):
            _bounded_query("SELECT * FROM information_schema.tables")

    def test_rejects_external_table_function(self):
        with self.assertRaisesRegex(ValueError, "qualified"):
            _bounded_query("SELECT * FROM read_parquet('private.parquet')")


if __name__ == "__main__":
    unittest.main()
