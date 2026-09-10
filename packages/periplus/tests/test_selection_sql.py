"""Standalone follow SQL is page-local and bounded before any admission."""
import unittest
import pyarrow as pa

from periplus.crawl.runtime.selection_sql import select_links, validate_follow_sql


def navigation_bytes(urls):
    table = pa.table({"target_url": urls})
    sink = pa.BufferOutputStream()
    with pa.ipc.new_file(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()


class SelectionSqlTests(unittest.TestCase):
    def test_filters_and_deduplicates_normalized_urls_in_result_order(self):
        payload = navigation_bytes(["https://example.com/b#one", "https://example.com/a", "https://example.com/b#two"])
        urls = select_links("SELECT target_url AS url FROM nav.links ORDER BY target_url", payload)
        self.assertEqual(urls, ("https://example.com/a", "https://example.com/b"))

    def test_catalogue_tables_files_environment_and_multiple_statements_are_rejected(self):
        for sql in (
            "SELECT requested_url AS url FROM web.observation",
            "SELECT target_url AS url FROM nav.links; SELECT 1",
            "SELECT target_url AS url FROM nav.links, read_csv('/tmp/input.csv')",
            "SELECT getenv('HOME') AS url FROM nav.links",
            "SELECT $url AS url FROM nav.links",
        ):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_follow_sql(sql)

    def test_request_cap_counts_distinct_normalized_urls_and_preserves_order(self):
        payload = navigation_bytes(["https://example.com/a#one"] * 1100 + [
            "https://example.com/a#two", "https://example.com/b", "https://example.com/c"])
        self.assertEqual(select_links("SELECT target_url AS url FROM nav.links", payload, max_links=2),
                         ("https://example.com/a", "https://example.com/b"))

    def test_request_can_select_and_checkpoint_more_than_one_thousand_links(self):
        from periplus.crawl.runtime.selection_contract import SelectionCheckpoint
        payload = navigation_bytes([f"https://example.com/{i}" for i in range(1500)])
        urls = select_links("SELECT target_url AS url FROM nav.links", payload, max_links=1200)
        self.assertEqual(len(urls), 1200)
        checkpoint = SelectionCheckpoint(urls=urls, cursor=1100)
        self.assertEqual(SelectionCheckpoint.model_validate_json(checkpoint.model_dump_json()), checkpoint)
        self.assertEqual(len(select_links("SELECT target_url AS url FROM nav.links", payload)), 1000)

    def test_follow_limit_bounds_are_validated(self):
        for limit in (0, 10001):
            with self.assertRaisesRegex(ValueError, "follow link limit"):
                select_links("SELECT target_url AS url FROM nav.links",
                             navigation_bytes(["https://example.com/"]), max_links=limit)

    def test_invalid_syntax_and_binding_are_terminal_selection_errors(self):
        payload = navigation_bytes(["https://example.com/"])
        for sql in ("SELECT ( AS url FROM nav.links", "SELECT missing_column AS url FROM nav.links"):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                select_links(sql, payload)
