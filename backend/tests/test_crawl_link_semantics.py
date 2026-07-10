from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from actions.crawl.service import _canonicalize_transient_links, _crawl_url
from dom import links_from_html


class CrawlLinkSemanticsTests(unittest.IsolatedAsyncioTestCase):
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
            patch("actions.crawl.service.crawl_metrics.navigation"),
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
