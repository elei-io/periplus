"""Bounded corpus selection uses the query service and freezes its provenance."""
import json
import unittest
from unittest.mock import patch

import httpx

from periplus.crawl.runtime.seed_query import SeedQueryClient, SeedQueryUnavailable
from periplus.query.service import QueryRequest


def result(**changes):
    return dict(query_id="query-17", sql="SELECT ? AS url", parameters=["https://example.com/"],
                diagnostics=[], plan="plan", columns=["url"], types=["VARCHAR"],
                rows=[["https://example.com/"]], truncated=False, elapsed_ms=1,
                source_snapshot=17, row_count=1, result_bytes=26) | changes


class SeedQueryTests(unittest.TestCase):
    def client(self, response):
        seen = []
        def handle(request):
            seen.append(request)
            return response
        client = SeedQueryClient("http://query.test", "credential", transport=httpx.MockTransport(handle))
        self.addCleanup(client.close)
        return client, seen

    def test_anonymous_parameter_cast_reaches_seed_service_unchanged(self):
        client, seen = self.client(httpx.Response(200, json=result()))
        payload = QueryRequest(sql="SELECT ?::VARCHAR AS url", parameters=["https://example.com/"])
        checkpoint = client.select(payload)
        self.assertEqual(checkpoint.urls, ("https://example.com/",))
        self.assertEqual(json.loads(seen[0].content), payload.model_dump())

    def test_parameters_snapshot_identity_and_time_are_frozen(self):
        client, seen = self.client(httpx.Response(200, json=result()))
        payload = QueryRequest(sql="SELECT ? AS url", parameters=["https://example.com/"])
        checkpoint = client.select(payload)
        self.assertEqual(checkpoint.urls, ("https://example.com/",))
        self.assertEqual(checkpoint.source_snapshot, "17")
        self.assertEqual(checkpoint.source_query_id, "query-17")
        self.assertIsNotNone(checkpoint.selected_at.utcoffset())
        self.assertEqual(json.loads(seen[0].content), payload.model_dump())
        self.assertEqual(seen[0].headers["authorization"], "Bearer credential")

    def test_truncation_wrong_columns_and_invalid_urls_are_not_partial_admission(self):
        for changes in ({"truncated": True}, {"columns": ["other"]}, {"rows": [["file:///secret"]]}):
            client, _ = self.client(httpx.Response(200, json=result(**changes)))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                client.select(QueryRequest(sql="SELECT ? AS url"))

    def test_operational_failures_remain_retryable_and_do_not_leak_response(self):
        for status in (401, 429, 500, 503, 302):
            client, _ = self.client(httpx.Response(status, text="secret storage URL"))
            with self.subTest(status=status), self.assertRaises(SeedQueryUnavailable) as error:
                client.select(QueryRequest(sql="SELECT 'https://example.com/' AS url"))
            self.assertNotIn("secret", str(error.exception))
        client, _ = self.client(httpx.Response(200, json={"broken": True}))
        with self.assertRaises(SeedQueryUnavailable):
            client.select(QueryRequest(sql="SELECT 1 AS url"))

    def test_response_bytes_are_bounded_before_json_decoding(self):
        client, _ = self.client(httpx.Response(200, content=b"x" * 1025))
        with patch("periplus.crawl.runtime.seed_query.MAX_RESPONSE_BYTES", 1024), self.assertRaises(SeedQueryUnavailable):
            client.select(QueryRequest(sql="SELECT 1 AS url"))

    def test_non_select_and_missing_credentials_do_not_call_service(self):
        client, seen = self.client(httpx.Response(200, json=result()))
        with self.assertRaises(ValueError):
            client.select(QueryRequest(sql="SHOW TABLES"))
        client.token = None
        with self.assertRaises(SeedQueryUnavailable):
            client.select(QueryRequest(sql="SELECT 1 AS url"))
        self.assertEqual(seen, [])

    def test_collection_intent_and_query_envelope_have_explicit_byte_limits(self):
        from periplus.crawl.control.collections.schemas import CollectionSpec
        with self.assertRaisesRegex(ValueError, "require seed SQL"):
            CollectionSpec(seed_parameters=("orphan",))
        with self.assertRaisesRegex(ValueError, "query request budget"):
            CollectionSpec(seed_sql="SELECT ? AS url", seed_parameters=("x" * (121 * 1024),))
        with self.assertRaisesRegex(ValueError, "intent exceeds"):
            CollectionSpec(seed_urls=("https://example.com/" + "x" * (257 * 1024),))
