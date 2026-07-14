from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from actions.crawl.schemas import CrawlPage
from actions.crawl.service import _canonicalize_transient_links, _crawl_url, _persist_page
from actions.shared.cache import ResolvedCachePolicy
from control.crawl_policies.schemas import BrowserProfileConfig, HttpProfileConfig
from dom import links_from_html


class CrawlLinkSemanticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_sample_records_typed_outcome_and_sampling_provenance(self) -> None:
        graph_id, graph_run_id, graph_node_id, crawl_request_id, trial_id = (
            uuid4() for _ in range(5)
        )
        page = CrawlPage(
            url="https://example.com/",
            success=False,
            status_code=503,
            duration_seconds=0.2,
            error="HTTP 503",
            failure_code="http_status",
            failure_stage="request",
            failure_retryable=True,
        )
        pipeline = SimpleNamespace(
            store_raw=AsyncMock(),
            enqueue_stored=AsyncMock(),
        )
        trial = {
            "trial_id": str(trial_id),
            "sampler_version": 2,
            "sample_share": 0.01,
            "candidate_strategy": "next_more_expensive_template",
            "candidate_template": "static_fast",
            "template_registry_version": 1,
        }
        with (
            patch(
                "actions.crawl.service._repository_retry_page",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "actions.crawl.service._run_envelope",
                return_value=SimpleNamespace(
                    graph_id=graph_id,
                    graph_run_id=graph_run_id,
                    graph_node_id=graph_node_id,
                    crawl_request_id=crawl_request_id,
                    purpose="sample",
                    trial=trial,
                    source_crawl_id=None,
                    source_edge_id=None,
                ),
            ),
        ):
            await _persist_page(
                MagicMock(),
                crawl_request_id=crawl_request_id,
                requested_url=page.url,
                page=page,
                profile="http",
                concurrency=4,
                profile_config=HttpProfileConfig(),
                repository_pipeline=pipeline,
                cache_policy=ResolvedCachePolicy(mode="refresh", max_age_seconds=120),
                retain_html=False,
                include_links=False,
            )

        pipeline.store_raw.assert_not_awaited()
        record = pipeline.enqueue_stored.await_args.args[0]
        self.assertEqual(record.outcome, "failed")
        self.assertEqual(record.failure_code, "http_status")
        self.assertTrue(record.failure_retryable)
        self.assertEqual(record.trial_id, trial_id)
        self.assertEqual(record.trial_sample_rate, 0.01)
        self.assertEqual(record.trial_candidate_template, "static_fast")

    async def test_persisted_page_can_release_retained_html(self) -> None:
        graph_id = uuid4()
        graph_run_id = uuid4()
        graph_node_id = uuid4()
        crawl_request_id = uuid4()
        page = CrawlPage(
            url="https://example.com/",
            success=True,
            status_code=200,
            duration_seconds=0.1,
            html="<html><body>ok</body></html>",
            crawl={},
        )
        pipeline = SimpleNamespace(
            store_raw=AsyncMock(),
            enqueue_stored=AsyncMock(),
        )

        with (
            patch(
                "actions.crawl.service._repository_retry_page",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "actions.crawl.service._run_envelope",
                return_value=SimpleNamespace(
                    graph_id=graph_id,
                    graph_run_id=graph_run_id,
                    graph_node_id=graph_node_id,
                    crawl_request_id=crawl_request_id,
                    purpose="use",
                    trial=None,
                    source_crawl_id=None,
                    source_edge_id=None,
                ),
            ),
        ):
            result = await _persist_page(
                MagicMock(),
                crawl_request_id=crawl_request_id,
                requested_url=page.url,
                page=page,
                profile="http",
                concurrency=4,
                profile_config=HttpProfileConfig(),
                repository_pipeline=pipeline,
                cache_policy=ResolvedCachePolicy(mode="prefer", max_age_seconds=120),
                retain_html=False,
                include_links=False,
            )

        self.assertIsNone(result.html)
        pipeline.store_raw.assert_awaited_once()
        pipeline.enqueue_stored.assert_awaited_once()
        record = pipeline.enqueue_stored.await_args.args[0]
        self.assertEqual(record.crawl_id, crawl_request_id)
        self.assertEqual(record.graph_id, graph_id)
        self.assertEqual(record.graph_run_id, graph_run_id)
        self.assertEqual(record.graph_node_id, graph_node_id)
        self.assertEqual(record.crawl_request_id, crawl_request_id)
        self.assertEqual(record.profile, "http")
        self.assertEqual(record.template, "http_fast")
        self.assertEqual(record.outcome, "success")
        self.assertEqual(len(record.config_hash), 64)

    async def test_fresh_crawl_replaces_transient_links_with_canonical_projection(self) -> None:
        html = (
            '<html><head><base href="/assets/"></head><body>'
            '<a href="guide"><span>Nested</span> text</a>'
            "</body></html>"
        )
        result = SimpleNamespace(
            url="https://example.com/start",
            success=False,
            status_code=200,
            redirected_url=None,
            redirected_status_code=None,
            links={"internal": [{"href": "wrong", "text": "wrong"}], "external": []},
            media={},
            metadata={},
            response_headers={},
            downloaded_files=None,
            js_execution_result=None,
            extracted_content=None,
            error_message="",
            session_id=None,
            network_requests=None,
            console_messages=None,
            tables=None,
            head_fingerprint=None,
            cached_at=None,
            cache_status=None,
            crawl_stats=None,
            html=html,
        )

        with (
            patch(
                "actions.crawl.service.crawl_single_url",
                new=AsyncMock(return_value=result),
            ),
        ):
            page = await _crawl_url(
                crawler=object(),  # type: ignore[arg-type]
                http_client=object(),  # type: ignore[arg-type]
                url=result.url,
                profile="browser",
                config=BrowserProfileConfig(),
                progress_reporter=None,
            )

        page = await _canonicalize_transient_links(page)
        assert page.crawl is not None
        self.assertTrue(page.success)
        self.assertIsNone(page.error)
        self.assertIsNone(page.failure_code)
        self.assertEqual(
            page.crawl["links"],
            links_from_html(html, page_url=result.url),
        )
        self.assertEqual(page.crawl["links"]["internal"][0]["text"], "Nested text")


if __name__ == "__main__":
    unittest.main()
