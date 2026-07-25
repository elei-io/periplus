import asyncio
import base64
from contextlib import asynccontextmanager
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from actions.crawl.schemas import AcquisitionAttemptEvidence, CrawlPage, CrawlStepEvidence
from actions.crawl.service import (
    PlaywrightRuntimeLost,
    _acquire,
    _status_outcome,
    crawl_graph_request,
)
from control.crawl_policies.schemas import CrawlPolicySnapshot, EffectivePolicySnapshot
from control.domain_policies.schemas import DomainPolicySnapshot
from runtime.context import GraphExecutionContext


def policy() -> CrawlPolicySnapshot:
    return CrawlPolicySnapshot(
        id=uuid4(), slug="default", scheme="*", host="*", path_prefix="/",
        path_mode="prefix",
    )


def effective_policy() -> EffectivePolicySnapshot:
    return EffectivePolicySnapshot(
        crawl=policy(),
        domain=DomainPolicySnapshot(
            id=uuid4(), slug="default-domain", host_match="*",
            maximum_concurrency=4, minimum_request_interval_seconds=0,
        ),
    )


class CdpAcquisitionTests(unittest.TestCase):
    def test_default_policy_enables_every_content_completion_move(self):
        completion = policy().content.completion
        self.assertTrue(completion.wait_dynamic.enabled)
        self.assertFalse(completion.wait_fixed.enabled)
        self.assertTrue(completion.scroll.enabled)
        self.assertTrue(completion.expand.enabled)
        self.assertTrue(completion.browser_interaction_enabled)

    def test_default_response_rules_retry_429_and_fail_other_client_errors(self):
        crawl_policy = policy()
        self.assertEqual(_status_outcome(429, crawl_policy), "retry")
        self.assertEqual(_status_outcome(404, crawl_policy), "fail")
        self.assertEqual(_status_outcome(200, crawl_policy), "accept")

    def test_preacquired_domain_permit_wraps_page_acquisition(self):
        effective = effective_policy()
        context = GraphExecutionContext(
            graph_id=uuid4(),
            graph_run_id=uuid4(),
            graph_node_id=uuid4(),
            crawl_request_id=uuid4(),
            effective_policy_snapshot_json=effective.model_dump(mode="json"),
        )
        events: list[str] = []

        @asynccontextmanager
        async def domain_permit():
            events.append("permit-enter")
            try:
                yield
            finally:
                events.append("permit-exit")

        async def acquire(*_args, **_kwargs):
            events.append("acquire")
            return CrawlPage(
                url="https://example.com/",
                success=False,
                duration_seconds=0.1,
                error="failed",
                failure_code="navigation_failed",
                failure_stage="navigation",
                failure_retryable=False,
                outcome="failed",
                attempt_evidence=AcquisitionAttemptEvidence(
                    attempt=1,
                    started_at="2026-07-25T00:00:00Z",
                    completed_at="2026-07-25T00:00:01Z",
                    requested_url="https://example.com/",
                    final_url="https://example.com/",
                    outcome="failed",
                    failure_code="navigation_failed",
                ),
            )

        async def scenario():
            pipeline = AsyncMock()
            with patch("actions.crawl.service._acquire", side_effect=acquire):
                await crawl_graph_request(
                    session=None,
                    url="https://example.com/",
                    context=context,
                    playwright=MagicMock(),
                    repository_pipeline=pipeline,
                    domain_permit=domain_permit(),
                )
            self.assertEqual(events, ["permit-enter", "acquire", "permit-exit"])
            pipeline.enqueue_stored.assert_awaited_once()

        asyncio.run(scenario())

    def test_all_completion_methods_disabled_omit_browser_only_cdp_calls(self):
        static_policy = CrawlPolicySnapshot(
            id=uuid4(),
            slug="static",
            scheme="*",
            host="*",
            path_prefix="/",
            path_mode="prefix",
            content={
                "completion": {
                    "wait_dynamic": {"enabled": False},
                    "wait_fixed": {"enabled": False},
                    "scroll": {"enabled": False},
                    "expand": {"enabled": False},
                }
            },
        )
        browser = AsyncMock()
        page = AsyncMock()
        page.url = "https://example.com/"
        page.content.return_value = "<html><body>static</body></html>"
        response = MagicMock(status=200)
        response.header_value = AsyncMock(
            side_effect=lambda name: "text/html" if name == "content-type" else None
        )
        page.goto.return_value = response
        browser.new_page.return_value = page
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)

        async def scenario():
            result = await _acquire(
                "https://example.com/",
                static_policy,
                attempt_number=1,
                playwright=playwright,
            )
            self.assertTrue(result.success)
            self.assertEqual(result.steps, ())
            browser.new_page.assert_awaited_once_with()
            page.goto.assert_awaited_once_with(
                "https://example.com/",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            page.evaluate.assert_not_awaited()
            page.wait_for_timeout.assert_not_awaited()
            playwright.chromium.connect_over_cdp.assert_awaited_once()
            browser.close.assert_awaited_once_with()

        asyncio.run(scenario())

    def test_disabled_attachment_type_is_a_clean_skip(self):
        browser = AsyncMock()
        page = AsyncMock()
        page.url = "about:blank"
        download = AsyncMock()
        download.url = "https://example.com/form.docx"
        response = MagicMock(
            status=200,
            url="https://example.com/form.docx",
        )
        response.request.redirected_to = None
        response.header_value = AsyncMock(
            side_effect=lambda name: (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                if name == "content-type"
                else None
            )
        )

        async def wait_for_event(event, **_kwargs):
            return download if event == "download" else response

        page.wait_for_event.side_effect = wait_for_event
        page.goto.side_effect = PlaywrightError("Page.goto: Download is starting")
        browser.new_page.return_value = page
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)

        async def scenario():
            result = await _acquire(
                "https://example.com/form.docx",
                policy(),
                attempt_number=1,
                playwright=playwright,
            )
            self.assertTrue(result.success)
            self.assertEqual(result.outcome, "skipped")
            self.assertEqual(
                result.response_media_type,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            self.assertEqual(
                result.attempt_evidence.failure_code,
                "unsupported_content_type",
            )
            download.cancel.assert_awaited_once_with()
            download.save_as.assert_not_awaited()

        asyncio.run(scenario())

    def test_enabled_attachment_type_is_captured_as_an_artifact(self):
        media_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        attachment_policy = CrawlPolicySnapshot(
            id=uuid4(),
            slug="documents",
            scheme="*",
            host="*",
            path_prefix="/",
            path_mode="prefix",
            content={
                "accepted_content_types": [
                    "text/html",
                    "application/xhtml+xml",
                    media_type,
                ]
            },
        )
        browser = AsyncMock()
        page = AsyncMock()
        page.url = "about:blank"
        session = MagicMock()
        session.send = AsyncMock(return_value={})
        page.context.new_cdp_session.return_value = session
        download = AsyncMock()
        download.url = "https://example.com/form.docx"
        download.failure.return_value = None
        payload = b"PK\x03\x04docx"

        async def save_as(path):
            Path(path).write_bytes(payload)

        download.save_as.side_effect = save_as
        response = MagicMock(
            status=200,
            url="https://cdn.example.com/form.docx",
        )
        response.request.redirected_to = None
        response.header_value = AsyncMock(
            side_effect=lambda name: media_type if name == "content-type" else None
        )
        redirect = MagicMock(
            status=302,
            url="https://example.com/form.docx",
        )
        redirect.request.redirected_to = response.request
        response.request.response = AsyncMock(return_value=response)

        async def wait_for_event(event, **_kwargs):
            return download if event == "download" else redirect

        page.wait_for_event.side_effect = wait_for_event
        page.goto.side_effect = PlaywrightError("Page.goto: Download is starting")
        browser.new_page.return_value = page
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)

        async def scenario():
            result = await _acquire(
                "https://example.com/form.docx",
                attachment_policy,
                attempt_number=1,
                playwright=playwright,
            )
            self.assertTrue(result.success)
            self.assertEqual(result.outcome, "success")
            self.assertEqual(result.url, "https://cdn.example.com/form.docx")
            self.assertEqual(result.response_media_type, media_type)
            self.assertEqual(result.artifact, payload)
            download.save_as.assert_awaited_once()
            download.cancel.assert_not_awaited()

        asyncio.run(scenario())

    def test_inline_pdf_captures_network_bytes_before_chrome_viewer(self):
        media_type = "application/pdf"
        pdf_policy = CrawlPolicySnapshot(
            id=uuid4(),
            slug="pdfs",
            scheme="*",
            host="*",
            path_prefix="/",
            path_mode="prefix",
            content={
                "accepted_content_types": [
                    "text/html",
                    "application/xhtml+xml",
                    media_type,
                ]
            },
        )
        browser = AsyncMock()
        page = AsyncMock()
        page.url = "https://example.com/report.pdf"
        session = MagicMock()
        session.send = AsyncMock()
        paused_handler = None
        payload = b"%PDF-1.7\r\nraw-pdf"
        viewer = b"""<!doctype html><link rel="stylesheet"
href="chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/pdf_embedder.css">"""

        def register_handler(event, handler):
            nonlocal paused_handler
            self.assertEqual(event, "Fetch.requestPaused")
            paused_handler = handler

        session.on.side_effect = register_handler

        async def send(method, params):
            if method == "Fetch.getResponseBody":
                self.assertEqual(params, {"requestId": "pdf-request"})
                return {
                    "body": base64.b64encode(payload).decode(),
                    "base64Encoded": True,
                }
            return {}

        session.send.side_effect = send
        page.context.new_cdp_session.return_value = session
        response = MagicMock(status=200)
        response.header_value = AsyncMock(
            side_effect=lambda name: media_type if name == "content-type" else None
        )
        response.body = AsyncMock(return_value=viewer)

        async def goto(*_args, **_kwargs):
            self.assertIsNotNone(paused_handler)
            await paused_handler(
                {
                    "requestId": "pdf-request",
                    "responseStatusCode": 200,
                    "responseHeaders": [
                        {"name": "Content-Type", "value": media_type}
                    ],
                }
            )
            return response

        page.goto.side_effect = goto
        browser.new_page.return_value = page
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)

        async def scenario():
            result = await _acquire(
                "https://example.com/report.pdf",
                pdf_policy,
                attempt_number=1,
                playwright=playwright,
            )
            self.assertTrue(result.success)
            self.assertEqual(result.response_media_type, media_type)
            self.assertEqual(result.artifact, payload)
            response.body.assert_not_awaited()
            session.send.assert_any_await(
                "Fetch.enable",
                {
                    "patterns": [
                        {
                            "urlPattern": "*",
                            "resourceType": "Document",
                            "requestStage": "Response",
                        }
                    ]
                },
            )
            session.send.assert_any_await(
                "Fetch.continueRequest",
                {"requestId": "pdf-request"},
            )

        asyncio.run(scenario())

    def test_dead_local_playwright_driver_is_not_a_crawl_failure(self):
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp = AsyncMock(
            side_effect=PlaywrightError(
                "BrowserType.connect_over_cdp: Connection closed while reading "
                "from the driver"
            )
        )

        async def scenario():
            with self.assertRaisesRegex(
                PlaywrightRuntimeLost, "driver connection was lost"
            ):
                await _acquire(
                    "https://example.com/",
                    policy(),
                    attempt_number=1,
                    playwright=playwright,
                )

        asyncio.run(scenario())

    def test_remote_cdp_connection_failure_remains_retryable(self):
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp = AsyncMock(
            side_effect=PlaywrightError("WebSocket error: connect ECONNREFUSED")
        )

        async def scenario():
            result = await _acquire(
                "https://example.com/",
                policy(),
                attempt_number=1,
                playwright=playwright,
            )
            self.assertFalse(result.success)
            self.assertEqual(result.failure_code, "cdp_connection_failed")
            self.assertTrue(result.failure_retryable)

        asyncio.run(scenario())

    def test_navigation_timeout_accepts_a_meaningful_completed_document(self):
        static_policy = CrawlPolicySnapshot(
            id=uuid4(),
            slug="static",
            scheme="*",
            host="*",
            path_prefix="/",
            path_mode="prefix",
            content={
                "completion": {
                    "navigation": {"timeout_ms": 45_000},
                    "wait_dynamic": {"enabled": False},
                    "wait_fixed": {"enabled": False},
                    "scroll": {"enabled": False},
                    "expand": {"enabled": False},
                }
            },
        )
        browser = AsyncMock()
        page = AsyncMock()
        page.url = "https://example.com/loaded"
        page.goto.side_effect = PlaywrightTimeoutError("navigation exceeded budget")
        page.content.return_value = (
            "<html><body><main><p>usable</p></main></body></html>"
        )
        browser.new_page.return_value = page
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)

        async def scenario():
            result = await _acquire(
                "https://example.com/",
                static_policy,
                attempt_number=1,
                playwright=playwright,
            )
            self.assertTrue(result.success)
            self.assertEqual(result.url, "https://example.com/loaded")
            page.goto.assert_awaited_once_with(
                "https://example.com/",
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            page.content.assert_awaited_once_with()
            page.evaluate.assert_not_awaited()

        asyncio.run(scenario())

    def test_navigation_timeout_rejects_an_unusable_document(self):
        static_policy = CrawlPolicySnapshot(
            id=uuid4(),
            slug="static",
            scheme="*",
            host="*",
            path_prefix="/",
            path_mode="prefix",
            content={
                "completion": {
                    "wait_dynamic": {"enabled": False},
                    "wait_fixed": {"enabled": False},
                    "scroll": {"enabled": False},
                    "expand": {"enabled": False},
                }
            },
        )
        browser = AsyncMock()
        page = AsyncMock()
        page.url = "about:blank"
        page.goto.side_effect = PlaywrightTimeoutError("navigation exceeded budget")
        browser.new_page.return_value = page
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)

        async def scenario():
            result = await _acquire(
                "https://example.com/",
                static_policy,
                attempt_number=1,
                playwright=playwright,
            )
            self.assertFalse(result.success)
            self.assertEqual(result.failure_code, "navigation_timeout")
            self.assertTrue(result.failure_retryable)
            page.content.assert_not_awaited()

        asyncio.run(scenario())

    def test_completion_recovers_from_execution_context_replacement(self):
        crawl_policy = CrawlPolicySnapshot(
            id=uuid4(),
            slug="dynamic",
            scheme="*",
            host="*",
            path_prefix="/",
            path_mode="prefix",
            content={
                "completion": {
                    "navigation": {"context_replacement_retries": 1},
                    "wait_dynamic": {"enabled": True},
                    "wait_fixed": {"enabled": False},
                    "scroll": {"enabled": False},
                    "expand": {"enabled": False},
                }
            },
        )
        browser = AsyncMock()
        page = AsyncMock()
        page.url = "https://example.com/"
        page.content.return_value = "<html><body>ready</body></html>"
        response = MagicMock(status=200)
        response.header_value = AsyncMock(
            side_effect=lambda name: "text/html" if name == "content-type" else None
        )
        page.goto.return_value = response
        browser.new_page.return_value = page
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)
        step = CrawlStepEvidence(
            attempt_number=1,
            step_ordinal=1,
            method="wait_dynamic",
            config_hash="a" * 64,
            config_json={"enabled": True},
            started_at="2026-07-17T00:00:00Z",
            duration_ms=10,
            iterations=1,
            stop_reason="stable",
            before_element_count=4,
            after_element_count=4,
            before_text_chars=5,
            after_text_chars=5,
            before_link_count=0,
            after_link_count=0,
            before_scroll_height=100,
            after_scroll_height=100,
        )

        async def scenario():
            wait_dynamic = AsyncMock(
                side_effect=[
                    PlaywrightError("Execution context was destroyed"),
                    step,
                ]
            )
            with patch("actions.crawl.service._wait_dynamic", wait_dynamic):
                result = await _acquire(
                    "https://example.com/",
                    crawl_policy,
                    attempt_number=1,
                    playwright=playwright,
                )
            self.assertTrue(result.success)
            self.assertEqual(result.steps, (step,))
            self.assertEqual(wait_dynamic.await_count, 2)
            page.wait_for_load_state.assert_awaited_once_with(
                "domcontentloaded",
                timeout=30_000,
            )
            page.wait_for_timeout.assert_awaited_once_with(1_000)

        asyncio.run(scenario())

    def test_exhausted_context_replacement_has_distinct_failure_code(self):
        crawl_policy = CrawlPolicySnapshot(
            id=uuid4(),
            slug="dynamic",
            scheme="*",
            host="*",
            path_prefix="/",
            path_mode="prefix",
            content={
                "completion": {
                    "navigation": {"context_replacement_retries": 0},
                    "wait_dynamic": {"enabled": True},
                    "wait_fixed": {"enabled": False},
                    "scroll": {"enabled": False},
                    "expand": {"enabled": False},
                }
            },
        )
        browser = AsyncMock()
        page = AsyncMock()
        page.url = "https://example.com/"
        page.content.return_value = "<html></html>"
        response = MagicMock(status=200)
        response.header_value = AsyncMock(
            side_effect=lambda name: "text/html" if name == "content-type" else None
        )
        page.goto.return_value = response
        browser.new_page.return_value = page
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)

        async def scenario():
            with patch(
                "actions.crawl.service._wait_dynamic",
                AsyncMock(
                    side_effect=PlaywrightError(
                        "Execution context was destroyed"
                    )
                ),
            ):
                result = await _acquire(
                    "https://example.com/",
                    crawl_policy,
                    attempt_number=1,
                    playwright=playwright,
                )
            self.assertFalse(result.success)
            self.assertEqual(result.failure_code, "execution_context_replaced")
            self.assertEqual(result.failure_stage, "completion")
            self.assertTrue(result.failure_retryable)

        asyncio.run(scenario())

    def test_exhausted_context_replacement_accepts_meaningful_html_snapshot(self):
        crawl_policy = CrawlPolicySnapshot(
            id=uuid4(),
            slug="dynamic",
            scheme="*",
            host="*",
            path_prefix="/",
            path_mode="prefix",
            content={
                "completion": {
                    "navigation": {"context_replacement_retries": 0},
                    "wait_dynamic": {"enabled": True},
                    "wait_fixed": {"enabled": False},
                    "scroll": {"enabled": False},
                    "expand": {"enabled": False},
                }
            },
        )
        browser = AsyncMock()
        page = AsyncMock()
        page.url = "https://example.com/settled"
        page.content.return_value = (
            "<html><head><title>Ready</title></head>"
            "<body><main><p>Useful content</p></main></body></html>"
        )
        response = MagicMock(status=200)
        response.header_value = AsyncMock(
            side_effect=lambda name: "text/html" if name == "content-type" else None
        )
        page.goto.return_value = response
        browser.new_page.return_value = page
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)

        async def scenario():
            with patch(
                "actions.crawl.service._wait_dynamic",
                AsyncMock(
                    side_effect=PlaywrightError(
                        "Execution context was destroyed"
                    )
                ),
            ):
                result = await _acquire(
                    "https://example.com/",
                    crawl_policy,
                    attempt_number=1,
                    playwright=playwright,
                )
            self.assertTrue(result.success)
            self.assertEqual(result.url, "https://example.com/settled")
            self.assertIn("Useful content", result.html or "")

        asyncio.run(scenario())

    def test_success_persists_attempt_and_no_failure_provenance(self):
        effective = effective_policy()
        context = GraphExecutionContext(
            graph_id=uuid4(), graph_run_id=uuid4(), graph_node_id=uuid4(),
            crawl_request_id=uuid4(),
            effective_policy_snapshot_json=effective.model_dump(mode="json"),
        )
        pipeline = AsyncMock()
        evidence = AcquisitionAttemptEvidence(
            attempt=1,
            started_at="2026-07-17T00:00:00Z",
            completed_at="2026-07-17T00:00:01Z",
            requested_url="https://example.com/",
            final_url="https://example.com/",
            status_code=200,
            response_media_type="text/html",
            outcome="success",
        )
        result = CrawlPage(
            url="https://example.com/", success=True, status_code=200,
            duration_seconds=0.1, html="<html><body>ok</body></html>",
            response_media_type="text/html", attempt_evidence=evidence,
            steps=(
                CrawlStepEvidence(
                    attempt_number=1,
                    step_ordinal=1,
                    method="wait_dynamic",
                    config_hash="a" * 64,
                    config_json={"enabled": True},
                    started_at="2026-07-17T00:00:00Z",
                    duration_ms=25,
                    iterations=3,
                    stop_reason="stable",
                    before_element_count=4,
                    after_element_count=8,
                    before_text_chars=0,
                    after_text_chars=2,
                    before_link_count=0,
                    after_link_count=0,
                    before_scroll_height=100,
                    after_scroll_height=100,
                ),
            ),
        )

        async def scenario():
            with patch("actions.crawl.service._acquire", AsyncMock(return_value=result)):
                page = await crawl_graph_request(
                    session=None, url="https://example.com/", context=context,
                    playwright=MagicMock(),
                    repository_pipeline=pipeline,
                )
            record = pipeline.enqueue_stored.await_args.args[0]
            attempts = pipeline.enqueue_stored.await_args.kwargs["crawl_attempts"]
            crawl_steps = pipeline.enqueue_stored.await_args.kwargs["crawl_steps"]
            raw_write = pipeline.store_raw.await_args.kwargs
            self.assertTrue(page.success)
            self.assertEqual(record.outcome, "success")
            self.assertEqual(raw_write["source_url"], "https://example.com/")
            self.assertEqual(raw_write["crawl_id"], context.crawl_request_id)
            self.assertEqual(raw_write["captured_at"], record.content_captured_at)
            self.assertEqual(raw_write["content_type"], "text/html")
            self.assertEqual(record.requested_url, "https://example.com/")
            self.assertEqual(record.url, "https://example.com/")
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0].status_code, 200)
            self.assertIsNone(record.failure_code)
            self.assertIsNone(record.failure_stage)
            self.assertIsNone(record.failure_retryable)
            self.assertIsNone(record.failure_detail)
            self.assertEqual(len(crawl_steps), 1)
            self.assertEqual(crawl_steps[0].crawl_id, context.crawl_request_id)
            self.assertEqual(crawl_steps[0].method, "wait_dynamic")

        asyncio.run(scenario())

    def test_artifact_write_uses_final_url_crawl_time_and_media_type(self):
        context = GraphExecutionContext(
            graph_id=uuid4(),
            graph_run_id=uuid4(),
            graph_node_id=uuid4(),
            crawl_request_id=uuid4(),
            effective_policy_snapshot_json=effective_policy().model_dump(mode="json"),
        )
        pipeline = AsyncMock()
        result = CrawlPage(
            url="https://example.com/final.pdf",
            success=True,
            status_code=200,
            duration_seconds=0.1,
            artifact=b"%PDF-1.7\n",
            response_media_type="application/pdf",
            attempt_evidence=AcquisitionAttemptEvidence(
                attempt=1,
                started_at="2026-07-25T00:00:00Z",
                completed_at="2026-07-25T00:00:01Z",
                requested_url="https://example.com/redirect",
                final_url="https://example.com/final.pdf",
                status_code=200,
                response_media_type="application/pdf",
                outcome="success",
            ),
        )

        async def scenario():
            with patch("actions.crawl.service._acquire", AsyncMock(return_value=result)):
                await crawl_graph_request(
                    session=None,
                    url="https://example.com/redirect",
                    context=context,
                    playwright=MagicMock(),
                    repository_pipeline=pipeline,
                )

            record = pipeline.enqueue_stored.await_args.args[0]
            artifact_write = pipeline.store_artifact.await_args.kwargs
            self.assertEqual(
                artifact_write["source_url"],
                "https://example.com/final.pdf",
            )
            self.assertEqual(artifact_write["crawl_id"], context.crawl_request_id)
            self.assertEqual(
                artifact_write["captured_at"],
                record.content_captured_at,
            )
            self.assertEqual(
                artifact_write["content_type"],
                "application/pdf",
            )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
