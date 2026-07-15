from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import httpx

from actions.crawl.service import _acquire_firecrawl, _acquire_http
from control.crawl_policies.schemas import (
    DEFAULT_HTTP_USER_AGENT,
    FirecrawlProfileConfig,
    HttpProfileConfig,
    parse_profile_config,
)


def http_config(**values) -> HttpProfileConfig:
    return HttpProfileConfig(user_agent=DEFAULT_HTTP_USER_AGENT, **values)


class AcquisitionProfileTests(unittest.IsolatedAsyncioTestCase):
    def test_artifact_capture_is_rejected_for_non_http_profiles(self) -> None:
        with self.assertRaisesRegex(ValueError, "supported only by the HTTP transport"):
            parse_profile_config(
                "browser",
                {"artifact_media_types": ["application/pdf"]},
            )

    def test_artifact_capture_media_types_must_be_unique(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be unique"):
            http_config(
                artifact_media_types=("image/*", "image/*"),
            )

    def test_http_profile_requires_a_descriptive_user_agent(self) -> None:
        with self.assertRaisesRegex(ValueError, "identify the crawler"):
            HttpProfileConfig(user_agent="python-httpx/0.28.1")

    def test_http_profile_rejects_sensitive_headers(self) -> None:
        with self.assertRaisesRegex(ValueError, "is not allowed"):
            http_config(headers={"Authorization": "Bearer secret"})

    async def test_http_profile_returns_remote_html(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["x-atlas-test"], "yes")
            self.assertEqual(request.headers["user-agent"], DEFAULT_HTTP_USER_AGENT)
            self.assertIn("text/html", request.headers["accept"])
            return httpx.Response(
                200,
                text="<html><body>ok</body></html>",
                headers={"Content-Type": "text/html; charset=utf-8"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await _acquire_http(
                client,
                "https://example.com/",
                http_config(headers={"x-atlas-test": "yes"}),
            )

        self.assertTrue(page.success)
        self.assertEqual(page.status_code, 200)
        self.assertIn("<body>ok</body>", page.html or "")

    async def test_http_profile_rejects_non_html_without_decoding_it(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"%PDF-1.7\x00binary",
                headers={"Content-Type": "application/pdf"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await _acquire_http(
                client,
                "https://example.com/report.pdf",
                http_config(),
            )

        self.assertFalse(page.success)
        self.assertIsNone(page.html)
        self.assertEqual(page.failure_code, "unsupported_content_type")
        self.assertFalse(page.failure_retryable)
        self.assertIn("application/pdf", page.error or "")

    async def test_http_profile_captures_allowed_pdf_as_exact_bytes(self) -> None:
        payload = b"%PDF-1.7\r\n\x00binary"

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=payload,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Disposition": 'attachment; filename="report.pdf"',
                },
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await _acquire_http(
                client,
                "https://example.com/report.pdf",
                http_config(artifact_media_types=("application/pdf",)),
            )

        self.assertTrue(page.success)
        self.assertIsNone(page.html)
        self.assertIsNotNone(page.artifact)
        assert page.artifact is not None
        self.assertEqual(page.artifact.content.read(), payload)
        self.assertEqual(page.artifact.media_type, "application/pdf")
        self.assertEqual(page.artifact.filename, "report.pdf")
        self.assertEqual(page.artifact.identity.size_bytes, len(payload))
        page.artifact.close()

    async def test_http_profile_enforces_policy_artifact_size(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"12345",
                headers={"Content-Type": "image/png"},
                request=request,
            )

        with patch.dict(os.environ, {"ATLAS_REPOSITORY_MAX_ARTIFACT_BYTES": "100"}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaisesRegex(ValueError, "artifact limit of 4 bytes"):
                    await _acquire_http(
                        client,
                        "https://example.com/image.png",
                        http_config(
                            artifact_media_types=("image/*",),
                            artifact_max_bytes=4,
                        ),
                    )

    async def test_http_status_precedes_content_type_classification(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                text="Please set a user-agent and respect our robot policy.",
                headers={"Content-Type": "text/plain"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await _acquire_http(client, "https://example.com/", http_config())

        self.assertFalse(page.success)
        self.assertEqual(page.status_code, 403)
        self.assertEqual(page.failure_code, "http_status")
        self.assertFalse(page.failure_retryable)
        self.assertIn("HTTP 403", page.error or "")
        self.assertIn("robot policy", page.error or "")

    async def test_http_retry_after_is_bounded_and_typed(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                text="slow down",
                headers={"Content-Type": "text/plain", "Retry-After": "600"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await _acquire_http(client, "https://example.com/", http_config())

        self.assertFalse(page.success)
        self.assertTrue(page.failure_retryable)
        self.assertEqual(page.retry_after_seconds, 300)

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
