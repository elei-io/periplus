from __future__ import annotations

import unittest
import os
from unittest.mock import patch

from fastapi.testclient import TestClient
from prometheus_client import generate_latest

from actions.crawl.schemas import CrawlPage
from api.app import app
from crawl_policies.service import generated_metric_slug
from observability import (
    capacity_metrics,
    crawl_metrics,
    metric_recorder_scope,
    repository_metrics,
)
from observability.prometheus import WorkerMetricAggregator
from observability.prometheus_source import collect_prometheus_metrics
from observability.recorder import InMemoryRecorder, MetricObservation


class ObservabilityTests(unittest.TestCase):
    def test_in_memory_recorder_validates_and_captures_semantic_crawl_metrics(self) -> None:
        recorder = InMemoryRecorder()
        page = CrawlPage(
            url="https://example.com",
            success=False,
            status_code=429,
            duration_seconds=1.5,
            error="rate limited",
        )
        with metric_recorder_scope(recorder):
            crawl_metrics.navigation(page=page, mode="static", domain_group="example")
            crawl_metrics.page_acquisition(
                page=page,
                duration_seconds=2.0,
                mode="static",
                source="network",
                domain_group="example",
            )

        self.assertEqual(
            recorder.total(
                "atlas_crawl_failures_total",
                reason="http_429",
                domain_group="example",
            ),
            1,
        )
        self.assertEqual(recorder.total("atlas_page_acquisitions_total"), 1)
        self.assertEqual(recorder.total("atlas_crawl_navigation_duration_seconds"), 1.5)

    def test_capacity_wait_records_one_duration_and_idempotent_waiter_snapshots(self) -> None:
        recorder = InMemoryRecorder()
        with metric_recorder_scope(recorder):
            wait = capacity_metrics.CapacityWaitMetrics(policy="example-policy")
            wait.blocked_by("browser")
            wait.blocked_by("browser")
            wait.finish("acquired")
            wait.finish("acquired")

        waits = recorder.matching("atlas_crawl_permit_wait_duration_seconds")
        self.assertEqual(len(waits), 1)
        self.assertEqual(waits[0].labels["blocked_scope"], "browser")
        snapshots = recorder.matching("atlas_crawl_permit_waiters")
        self.assertEqual([observation.value for observation in snapshots], [1, 0])

    def test_repository_cache_records_bounded_outcome_and_entry_age(self) -> None:
        recorder = InMemoryRecorder()
        with metric_recorder_scope(recorder):
            crawl_metrics.repository_cache(outcome="hit", age_seconds=42.5)
            crawl_metrics.repository_cache(outcome="refresh")

        self.assertEqual(
            recorder.total("atlas_repository_cache_lookups_total", outcome="hit"),
            1,
        )
        self.assertEqual(
            recorder.total(
                "atlas_repository_cache_entry_age_seconds",
                outcome="hit",
            ),
            42.5,
        )
        self.assertEqual(
            recorder.total(
                "atlas_repository_cache_lookups_total",
                outcome="refresh",
            ),
            1,
        )

    def test_repository_ingestion_metrics_are_bounded_and_batch_scoped(self) -> None:
        recorder = InMemoryRecorder()
        with metric_recorder_scope(recorder):
            repository_metrics.raw_write(
                outcome="deduplicated",
                duration_seconds=0.1,
                html_bytes=100,
                compressed_bytes=50,
            )
            repository_metrics.attempt(outcome="succeeded", queue_seconds=0.2)
            repository_metrics.batch(
                outcome="succeeded",
                duration_seconds=0.3,
                items=2,
                element_rows=20,
                staged_bytes=200,
            )
            repository_metrics.queue_state(
                pending=3,
                ack_pending=2,
                redelivered=1,
            )

        self.assertEqual(
            recorder.total(
                "atlas_repository_raw_writes_total",
                outcome="deduplicated",
            ),
            1,
        )
        self.assertEqual(
            recorder.total(
                "atlas_repository_ingestion_attempts_total",
                outcome="succeeded",
            ),
            1,
        )
        self.assertEqual(recorder.total("atlas_repository_ingestion_batch_items"), 2)
        self.assertEqual(recorder.total("atlas_repository_ingestion_jobs_pending"), 3)

    def test_worker_aggregator_sums_and_forgets_child_snapshots(self) -> None:
        aggregator = WorkerMetricAggregator()
        labels = {"scope": "policy", "policy": "example-policy"}
        aggregator.apply(
            MetricObservation(
                "atlas_crawl_permit_waiters", 2, labels, "snapshot", "child-one"
            )
        )
        aggregator.apply(
            MetricObservation(
                "atlas_crawl_permit_waiters", 1, labels, "snapshot", "child-two"
            )
        )
        payload = generate_latest(aggregator.registry).decode()
        self.assertIn(
            'atlas_crawl_permit_waiters{policy="example-policy",scope="policy"} 3.0',
            payload,
        )
        aggregator.forget_child("child-one")
        payload = generate_latest(aggregator.registry).decode()
        self.assertIn(
            'atlas_crawl_permit_waiters{policy="example-policy",scope="policy"} 1.0',
            payload,
        )

    def test_policy_metric_slug_is_bounded_and_collision_suffixed(self) -> None:
        slug = generated_metric_slug(
            "https://Sub.Example.COM/products/*",
            suffix="abcdef123456",
        )
        self.assertEqual(slug, "sub-example-com-abcdef12")

    def test_prometheus_enrichment_is_optional(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            metrics = collect_prometheus_metrics(3600)
        self.assertFalse(metrics.configured)
        self.assertFalse(metrics.available)

    def test_api_metrics_endpoint_is_available_but_not_in_public_openapi(self) -> None:
        self.assertNotIn("/metrics", app.openapi()["paths"])
        with patch(
            "api.routers.operational_metrics.api_metrics_payload",
            return_value=b"atlas_workers_live 1\n",
        ):
            response = TestClient(app).get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("atlas_workers_live 1", response.text)


if __name__ == "__main__":
    unittest.main()
