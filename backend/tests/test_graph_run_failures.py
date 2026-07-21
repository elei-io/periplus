from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from api.routers.graph_runs import _failure_record
from repository.catalogue.records import CrawlRecord, UrlRecord
from runtime.graph_queue import CrawlRequest


class GraphRunFailureApiTests(unittest.TestCase):
    def test_prefers_durable_ingestion_failure_provenance(self) -> None:
        request = _failed_request()
        captured_at = datetime(2026, 7, 17, tzinfo=UTC)
        crawl_id = uuid4()
        requested_url = UrlRecord.from_normalized_url(request.url)
        final_url = UrlRecord.from_normalized_url("https://example.com/final")
        state = SimpleNamespace(
            crawl=CrawlRecord(
                crawl_id=crawl_id,
                graph_id=uuid4(),
                graph_run_id=request.graph_run_id,
                graph_node_id=request.node_id,
                crawl_request_id=request.id,
                requested_url_id=requested_url.url_id,
                final_url_id=final_url.url_id,
                status_code=503,
                outcome="failed",
                failure_code="navigation_failed",
                failure_stage="navigation",
                failure_retryable=False,
                failure_detail="Execution context was destroyed.",
                captured_at=captured_at,
            ),
            urls=(requested_url, final_url),
        )

        result = _failure_record(request, state)

        self.assertEqual(result.crawl_id, crawl_id)
        self.assertEqual(result.requested_url, request.url)
        self.assertEqual(result.final_url, "https://example.com/final")
        self.assertEqual(result.failure_code, "navigation_failed")
        self.assertEqual(result.failure_detail, "Execution context was destroyed.")
        self.assertEqual(result.captured_at, captured_at)

    def test_falls_back_to_failed_request_when_ingestion_state_expired(self) -> None:
        request = _failed_request()

        result = _failure_record(request, None)

        self.assertEqual(result.crawl_id, request.id)
        self.assertEqual(result.requested_url, request.url)
        self.assertEqual(result.failure_stage, "acquisition")
        self.assertEqual(result.failure_detail, "browser unavailable")


def _failed_request() -> CrawlRequest:
    now = datetime.now(UTC)
    return CrawlRequest(
        id=uuid4(),
        graph_run_id=uuid4(),
        node_id=uuid4(),
        url="https://example.com/",
        effective_policy_snapshot_json={},
        status="failed",
        created_at=now,
        updated_at=now,
        error="browser unavailable",
        failure_stage="acquisition",
    )


if __name__ == "__main__":
    unittest.main()
