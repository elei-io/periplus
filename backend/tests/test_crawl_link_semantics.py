from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from actions.crawl.schemas import CrawlPage
from actions.crawl.service import _canonicalize_transient_links, _crawl_url, _persist_page
from actions.shared.cache import ResolvedCachePolicy
from dom import links_from_html


class CrawlLinkSemanticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_persisted_page_can_release_retained_html(self) -> None:
        run_id = uuid4()
        task_id = uuid4()
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
            submit_stored=AsyncMock(
                return_value=SimpleNamespace(
                    document_id="sha256:test",
                    repository_snapshot=1,
                    crawl_created=True,
                )
            ),
        )

        with (
            patch(
                "actions.crawl.service._repository_retry_page",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "actions.crawl.service._run_envelope",
                return_value=SimpleNamespace(
                    task_id=task_id,
                    task_revision=1,
                    primitive="crawl",
                    data_schema_id=None,
                ),
            ),
        ):
            result = await _persist_page(
                MagicMock(),
                task_run_id=run_id,
                index=0,
                requested_url=page.url,
                page=page,
                mode="static",
                wait="none",
                repository_pipeline=pipeline,
                cache_policy=ResolvedCachePolicy(mode="prefer", max_age_seconds=120),
                retain_html=False,
                include_links=False,
            )

        self.assertIsNone(result.html)
        pipeline.store_raw.assert_awaited_once()
        pipeline.submit_stored.assert_awaited_once()

    async def test_fresh_crawl_replaces_transient_links_with_canonical_projection(self) -> None:
        html = (
            '<html><head><base href="/assets/"></head><body>'
            '<a href="guide"><span>Nested</span> text</a>'
            "</body></html>"
        )
        result = SimpleNamespace(
            url="https://example.com/start",
            success=True,
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
            error_message=None,
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
                url=result.url,
                mode="static",
                wait="none",
                progress_reporter=None,
            )

        page = await _canonicalize_transient_links(page)
        assert page.crawl is not None
        self.assertEqual(
            page.crawl["links"],
            links_from_html(html, page_url=result.url),
        )
        self.assertEqual(page.crawl["links"]["internal"][0]["text"], "Nested text")


if __name__ == "__main__":
    unittest.main()
