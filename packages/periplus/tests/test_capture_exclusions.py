"""CDP request-stage exclusion and response-stage capture share one handler."""
import unittest
from unittest.mock import AsyncMock, Mock

from periplus.crawl.acquisition.capture import _NavigationArtifactCapture, _enable_navigation_document_capture
from periplus.crawl.control.collections.exclusions import UrlExclusion


class CaptureExclusionTests(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_hop_and_subresource_are_blocked_without_continuing(self):
        capture = _NavigationArtifactCapture(("text/html",), (UrlExclusion(host="blocked.example"),))
        capture.main_frame_id = "main"
        for resource in ("Script", "Document"):
            session = AsyncMock()
            await capture.handle_paused(session, {"requestId": "r", "redirectedRequestId": "previous",
                "frameId": "main", "resourceType": resource, "request": {"url": "https://blocked.example/a"}})
            session.send.assert_awaited_once_with("Fetch.failRequest", {"requestId": "r", "errorReason": "BlockedByClient"})
        self.assertTrue(capture.blocked_document)

    async def test_allowed_requests_continue_and_blocked_subframe_does_not_fail_main_navigation(self):
        capture = _NavigationArtifactCapture(("text/html",), (UrlExclusion(host="blocked.example"),))
        capture.main_frame_id = "main"
        session = AsyncMock()
        await capture.handle_paused(session, {"requestId": "r", "resourceType": "Document", "frameId": "child",
            "request": {"url": "https://blocked.example/a"}})
        self.assertFalse(capture.blocked_document)
        session.reset_mock()
        await capture.handle_paused(session, {"requestId": "allowed", "request": {"url": "https://allowed.example/a"}})
        session.send.assert_awaited_once_with("Fetch.continueRequest", {"requestId": "allowed"})

    async def test_non_html_response_capture_still_reads_body_and_continues(self):
        capture = _NavigationArtifactCapture(("application/pdf",), (UrlExclusion(host="blocked.example"),))
        session = AsyncMock()
        session.send.return_value = {"body": "JVBERg==", "base64Encoded": True}
        await capture.handle_paused(session, {"requestId": "pdf", "responseStatusCode": 200,
            "responseHeaders": [{"name": "content-type", "value": "application/pdf"}]})
        self.assertEqual(capture.body, b"%PDF")
        self.assertEqual(session.send.await_args_list[0].args[0], "Fetch.getResponseBody")
        self.assertEqual(session.send.await_args_list[1].args[0], "Fetch.continueRequest")

    async def test_html_only_exclusions_enable_request_interception_before_navigation(self):
        page = Mock()
        session = Mock()
        session.send = AsyncMock(return_value={"frameTree": {"frame": {"id": "main"}}})
        page.context.new_cdp_session = AsyncMock(return_value=session)
        capture = await _enable_navigation_document_capture(page, ("text/html",), (UrlExclusion(host="blocked.example"),))
        self.assertEqual(capture.main_frame_id, "main")
        session.send.assert_awaited_with("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})


class LiveCaptureExclusionTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(__import__("os").environ.get("PERIPLUS_TEST_CHROMIUM") == "1", "opt-in isolated Chromium test")
    async def test_real_chromium_blocks_redirect_and_script_before_server_receives_them(self):
        import asyncio
        from playwright.async_api import async_playwright, Error
        received = []
        async def serve(reader, writer):
            try:
                headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
                path = headers.split(b" ")[1].decode()
                received.append(path)
                if path == "/redirect":
                    response = b"HTTP/1.1 302 Found\r\nLocation: /blocked/page\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                else:
                    body = b'<html><body>Allowed page<script src="/blocked/script.js"></script></body></html>'
                    response = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                writer.write(response)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        origin = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless=True)
                try:
                    page = await browser.new_page()
                    capture = await _enable_navigation_document_capture(page, ("text/html",),
                        (UrlExclusion(host="127.0.0.1", path_prefix="/blocked"),))
                    await page.goto(origin + "/page", wait_until="load")
                    self.assertIn("Allowed page", await page.content())
                    self.assertFalse(capture.blocked_document)
                    with self.assertRaises(Error):
                        await page.goto(origin + "/redirect")
                    self.assertTrue(capture.blocked_document)
                    self.assertIn("/page", received)
                    self.assertIn("/redirect", received)
                    self.assertFalse(any(path.startswith("/blocked/") for path in received), received)
                finally:
                    await browser.close()
        finally:
            server.close()
            await server.wait_closed()
