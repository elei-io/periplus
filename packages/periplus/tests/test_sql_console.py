import unittest

from periplus.query.http import _bounded_query


class SqlConsoleValidationTests(unittest.TestCase):
    def test_accepts_read_only_public_catalogue_query(self):
        bounded = _bounded_query(
            """
            SELECT observation.requested_url, element.tag
            FROM web.observation AS observation
            JOIN content.html_element AS element
              ON element.content_id = observation.content_id
            """
        )

        self.assertIn("LIMIT 10001", bounded)
        self.assertIn("FROM web.observation", bounded)

    def test_rejects_mutation(self):
        with self.assertRaisesRegex(ValueError, "read-only query"):
            _bounded_query("DELETE FROM web.observation")

    def test_rejects_multiple_statements(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            _bounded_query(
                "SELECT * FROM web.observation; SELECT * FROM content.object"
            )

    def test_requires_public_schema_qualification(self):
        with self.assertRaisesRegex(ValueError, "qualified"):
            _bounded_query("SELECT * FROM visits")

    def test_rejects_non_public_schema(self):
        with self.assertRaisesRegex(ValueError, "web"):
            _bounded_query("SELECT * FROM information_schema.tables")

    def test_rejects_physical_schema(self):
        with self.assertRaisesRegex(ValueError, "web"):
            _bounded_query("SELECT * FROM material.html_elements")

    def test_accepts_content_relation(self):
        bounded = _bounded_query(
            "SELECT content_id, tag FROM content.html_element"
        )

        self.assertIn("FROM content.html_element", bounded)

    def test_accepts_describe_for_public_relation(self):
        self.assertEqual(
            _bounded_query("DESCRIBE web.observation;"),
            "DESCRIBE web.observation",
        )

        self.assertEqual(
            _bounded_query("DESCRIBE content.object;"),
            "DESCRIBE content.object",
        )

    def test_rejects_describe_for_physical_relation(self):
        with self.assertRaisesRegex(ValueError, "web"):
            _bounded_query("DESCRIBE material.html_elements")

    def test_accepts_explain_for_public_query(self):
        self.assertEqual(
            _bounded_query("EXPLAIN SELECT * FROM web.observation"),
            "EXPLAIN SELECT * FROM web.observation",
        )

    def test_accepts_explain_analyze_for_public_query(self):
        self.assertEqual(
            _bounded_query("EXPLAIN ANALYZE SELECT * FROM web.observation"),
            "EXPLAIN ANALYZE SELECT * FROM web.observation",
        )

    def test_rejects_explain_for_mutation(self):
        with self.assertRaisesRegex(ValueError, "read-only query"):
            _bounded_query("EXPLAIN DELETE FROM web.observation")

    def test_rejects_explain_for_physical_relation(self):
        with self.assertRaisesRegex(ValueError, "web"):
            _bounded_query("EXPLAIN SELECT * FROM ingest.visits")

    def test_accepts_summarize_for_public_relation(self):
        self.assertEqual(
            _bounded_query("SUMMARIZE web.observation"),
            "SUMMARIZE web.observation",
        )

        self.assertEqual(
            _bounded_query("SUMMARIZE content.html_element"),
            "SUMMARIZE content.html_element",
        )

    def test_accepts_show_tables_for_public_schema(self):
        self.assertEqual(
            _bounded_query("SHOW TABLES FROM web"),
            "SHOW TABLES FROM web",
        )
        self.assertEqual(
            _bounded_query("SHOW TABLES FROM content"),
            "SHOW TABLES FROM content",
        )

    def test_rejects_show_all_tables(self):
        with self.assertRaisesRegex(ValueError, "SHOW TABLES FROM web"):
            _bounded_query("SHOW ALL TABLES")

    def test_rejects_external_table_function(self):
        with self.assertRaisesRegex(ValueError, "qualified"):
            _bounded_query("SELECT * FROM read_parquet('private.parquet')")

    def test_rejects_internal_extension_function(self):
        with self.assertRaisesRegex(ValueError, "periplus_lint_query"):
            _bounded_query("SELECT periplus_lint_query('SELECT 42')")


if __name__ == "__main__":
    unittest.main()
