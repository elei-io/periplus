"""Historical eligibility requires a bounded, complete, snapshot-labelled result."""
import unittest
from unittest.mock import Mock

from periplus.crawl.runtime.background_seen import (
    BackgroundLookupUnavailable, HistoricalSeenResult, SeenCandidates, lookup_seen,
)
from periplus.crawl.runtime.selection_contract import SelectionCheckpoint


class BackgroundSeenTests(unittest.TestCase):
    def test_deduplication_bounds_and_parameterized_query(self):
        candidates = SeenCandidates(urls=("https://example.com/a#one", "https://example.com/a#two"))
        self.assertEqual(candidates.urls, ("https://example.com/a",))
        query = candidates.query()
        self.assertEqual(query.parameters, [["https://example.com/a"]])
        self.assertNotIn("https://example.com/a", query.sql)
        with self.assertRaises(ValueError):
            SeenCandidates(urls=tuple(f"https://example.com/{index}" for index in range(65)))
        with self.assertRaises(ValueError):
            SeenCandidates(urls=tuple("https://example.com/" + str(index) + "x" * 8000 for index in range(9)))

    def test_failure_missing_snapshot_and_unrequested_results_never_prove_absence(self):
        candidates = SeenCandidates(urls=("https://example.com/",))
        for selected in (
            Mock(side_effect=RuntimeError("lake unavailable")),
            Mock(return_value=SelectionCheckpoint(urls=(), source_query_id="q")),
            Mock(return_value=SelectionCheckpoint(urls=("https://other.example/",),
                                                 source_snapshot="3", source_query_id="q")),
        ):
            with self.assertRaises(BackgroundLookupUnavailable):
                lookup_seen(candidates, selected)
        result = lookup_seen(candidates, Mock(return_value=SelectionCheckpoint(
            urls=(), source_snapshot="7", source_query_id="q",
        )))
        self.assertEqual(result, HistoricalSeenResult(candidates=candidates, seen_urls=(), snapshot=7, query_id="q"))

    def test_background_traps_are_declined_without_rewriting_query_identity(self):
        from periplus.crawl.runtime.background_policy import background_rejection
        for url in ("https://example.com/calendar/2026", "https://example.com/calendar%2F2026", "https://example.com/a/a/a",
                    "https://example.com/?page=999", "https://example.com/?sessionid=abc",
                    "https://example.com/?tag=a&tag=b", "http://127.0.0.1/", "http://metadata.internal/"):
            with self.subTest(url=url):
                self.assertIsNotNone(background_rejection(url))
        for url in ("https://example.com/article?lang=fi", "https://example.com/?page=2",
                    "https://example.com/2026/09/story"):
            self.assertIsNone(background_rejection(url))
            self.assertEqual(SeenCandidates(urls=(url,)).urls, (url,))
