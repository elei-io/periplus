import unittest

from periplus.query.validation import _bounded_query


class SqlConsoleValidationTests(unittest.TestCase):
    def test_accepts_read_only_public_catalogue_query(self):
        bounded = _bounded_query(
            """
            SELECT observation.requested_url, element.tag
            FROM public_v1.capture AS observation
            JOIN public_v1.html_element AS element
              ON element.content_id = observation.content_id
            """
        )

        self.assertIn("LIMIT 10001", bounded)
        self.assertIn("FROM public_v1.capture", bounded)

    def test_rejects_mutation(self):
        with self.assertRaisesRegex(ValueError, "read-only query"):
            _bounded_query("DELETE FROM public_v1.capture")

    def test_rejects_multiple_statements(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            _bounded_query(
                "SELECT * FROM public_v1.capture; SELECT * FROM public_v1.html_node"
            )

    def test_requires_public_schema_qualification(self):
        with self.assertRaisesRegex(ValueError, "Unknown public_v1"):
            _bounded_query("SELECT * FROM visits")

    def test_rejects_non_public_schema(self):
        with self.assertRaisesRegex(ValueError, "public_v1"):
            _bounded_query("SELECT * FROM information_schema.tables")

    def test_rejects_physical_schema(self):
        with self.assertRaisesRegex(ValueError, "public_v1"):
            _bounded_query("SELECT * FROM material.html_elements")

    def test_accepts_content_relation(self):
        bounded = _bounded_query(
            "SELECT content_id, tag FROM public_v1.html_element"
        )

        self.assertIn("FROM public_v1.html_element", bounded)

    def test_accepts_describe_for_public_relation(self):
        self.assertEqual(
            _bounded_query("DESCRIBE public_v1.capture;"),
            "DESCRIBE public_v1.capture",
        )

        self.assertEqual(
            _bounded_query("DESCRIBE public_v1.html_node;"),
            "DESCRIBE public_v1.html_node",
        )

    def test_rejects_describe_for_physical_relation(self):
        with self.assertRaisesRegex(ValueError, "public_v1"):
            _bounded_query("DESCRIBE material.html_elements")

    def test_accepts_explain_for_public_query(self):
        self.assertEqual(
            _bounded_query("EXPLAIN SELECT * FROM public_v1.capture"),
            "EXPLAIN SELECT * FROM public_v1.capture",
        )

    def test_accepts_explain_analyze_for_public_query(self):
        self.assertEqual(
            _bounded_query("EXPLAIN ANALYZE SELECT * FROM public_v1.capture"),
            "EXPLAIN ANALYZE SELECT * FROM public_v1.capture",
        )

    def test_rejects_explain_for_mutation(self):
        with self.assertRaisesRegex(ValueError, "read-only query"):
            _bounded_query("EXPLAIN DELETE FROM public_v1.capture")

    def test_rejects_explain_for_physical_relation(self):
        with self.assertRaisesRegex(ValueError, "public_v1"):
            _bounded_query("EXPLAIN SELECT * FROM ingest.visits")

    def test_accepts_summarize_for_public_relation(self):
        self.assertEqual(
            _bounded_query("SUMMARIZE public_v1.capture"),
            "SUMMARIZE public_v1.capture",
        )

        self.assertEqual(
            _bounded_query("SUMMARIZE public_v1.html_element"),
            "SUMMARIZE public_v1.html_element",
        )

    def test_accepts_show_tables_for_public_schema(self):
        self.assertEqual(
            _bounded_query("SHOW TABLES FROM public_v1"),
            "SHOW TABLES FROM public_v1",
        )
        self.assertEqual(
            _bounded_query("SHOW TABLES FROM public_v1"),
            "SHOW TABLES FROM public_v1",
        )

    def test_rejects_show_all_tables(self):
        with self.assertRaisesRegex(ValueError, "SHOW TABLES FROM public_v1"):
            _bounded_query("SHOW ALL TABLES")

    def test_rejects_external_table_function(self):
        with self.assertRaisesRegex(ValueError, "Unknown public_v1"):
            _bounded_query("SELECT * FROM read_parquet('private.parquet')")



if __name__ == "__main__":
    unittest.main()
