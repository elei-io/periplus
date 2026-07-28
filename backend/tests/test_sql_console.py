import unittest

from atlas.query.http import _bounded_query


class SqlConsoleValidationTests(unittest.TestCase):
    def test_accepts_read_only_public_catalogue_query(self):
        bounded = _bounded_query(
            """
            WITH recent AS (
                SELECT visit_id, requested_url
                FROM web.visits
            )
            SELECT recent.requested_url, pages.hostname
            FROM recent
            JOIN web.pages AS pages
              ON pages.url = recent.requested_url
            """
        )

        self.assertIn("LIMIT 10001", bounded)
        self.assertIn("FROM web.visits", bounded)

    def test_rejects_mutation(self):
        with self.assertRaisesRegex(ValueError, "read-only query"):
            _bounded_query("DELETE FROM web.visits")

    def test_rejects_multiple_statements(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            _bounded_query(
                "SELECT * FROM web.visits; SELECT * FROM web.pages"
            )

    def test_requires_public_schema_qualification(self):
        with self.assertRaisesRegex(ValueError, "qualified"):
            _bounded_query("SELECT * FROM visits")

    def test_rejects_non_public_schema(self):
        with self.assertRaisesRegex(ValueError, "web"):
            _bounded_query("SELECT * FROM information_schema.tables")

    def test_rejects_physical_schema(self):
        with self.assertRaisesRegex(ValueError, "web"):
            _bounded_query("SELECT * FROM material.pages")

    def test_accepts_public_table_macro(self):
        bounded = _bounded_query(
            "SELECT * FROM web.page_history(NULL::UUID)"
        )

        self.assertIn("web.page_history", bounded)

    def test_accepts_describe_for_public_relation(self):
        self.assertEqual(
            _bounded_query("DESCRIBE web.pages;"),
            "DESCRIBE web.pages",
        )

    def test_rejects_describe_for_physical_relation(self):
        with self.assertRaisesRegex(ValueError, "web"):
            _bounded_query("DESCRIBE material.pages")

    def test_accepts_explain_for_public_query(self):
        self.assertEqual(
            _bounded_query("EXPLAIN SELECT * FROM web.pages"),
            "EXPLAIN SELECT * FROM web.pages",
        )

    def test_accepts_explain_analyze_for_public_query(self):
        self.assertEqual(
            _bounded_query("EXPLAIN ANALYZE SELECT * FROM web.pages"),
            "EXPLAIN ANALYZE SELECT * FROM web.pages",
        )

    def test_rejects_explain_for_mutation(self):
        with self.assertRaisesRegex(ValueError, "read-only query"):
            _bounded_query("EXPLAIN DELETE FROM web.pages")

    def test_rejects_explain_for_physical_relation(self):
        with self.assertRaisesRegex(ValueError, "web"):
            _bounded_query("EXPLAIN SELECT * FROM ingest.visits")

    def test_accepts_summarize_for_public_relation(self):
        self.assertEqual(
            _bounded_query("SUMMARIZE web.pages"),
            "SUMMARIZE web.pages",
        )

    def test_accepts_show_tables_for_public_schema(self):
        self.assertEqual(
            _bounded_query("SHOW TABLES FROM web"),
            "SHOW TABLES FROM web",
        )

    def test_rejects_show_all_tables(self):
        with self.assertRaisesRegex(ValueError, "SHOW TABLES FROM web"):
            _bounded_query("SHOW ALL TABLES")

    def test_rejects_external_table_function(self):
        with self.assertRaisesRegex(ValueError, "qualified"):
            _bounded_query("SELECT * FROM read_parquet('private.parquet')")


if __name__ == "__main__":
    unittest.main()
