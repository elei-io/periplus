from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import httpx

from actions.crawl.service import _acquire_firecrawl, _acquire_http
from control.crawl_policies.schemas import FirecrawlProfileConfig, HttpProfileConfig
class AcquisitionProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_profile_returns_remote_html(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["x-atlas-test"], "yes")
            return httpx.Response(
                200,
                text="<html><body>ok</body></html>",
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await _acquire_http(
                client,
                "https://example.com/",
                HttpProfileConfig(headers={"x-atlas-test": "yes"}),
            )

        self.assertTrue(page.success)
        self.assertEqual(page.status_code, 200)
        self.assertIn("<body>ok</body>", page.html or "")

    async def test_firecrawl_profile_requests_raw_html(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v2/scrape")
            self.assertEqual(request.headers["authorization"], "Bearer fc-test")
            self.assertIn(b'"formats":["rawHtml"]', request.content)
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "rawHtml": "<html><body>remote</body></html>",
                        "metadata": {
                            "url": "https://example.com/final",
                            "statusCode": 200,
                        },
                    },
                },
                request=request,
            )

        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "fc-test"}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                page = await _acquire_firecrawl(
                    client,
                    "https://example.com/",
                    FirecrawlProfileConfig(),
                )

        self.assertTrue(page.success)
        self.assertEqual(page.url, "https://example.com/final")
        self.assertIn("remote", page.html or "")

if __name__ == "__main__":
    unittest.main()
