"""Captures return frozen evidence; runtime acceptance owns publication."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from frontier_fixtures import policy_snapshot
from periplus.crawl.acquisition.context import AcquisitionContext
from periplus.crawl.acquisition.models import AcquisitionAttemptEvidence, AcquisitionResult, AcquisitionStepEvidence
from periplus.crawl.acquisition.service import acquire_page
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot


class AcquisitionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_returns_evidence_without_publishing(self):
        now = datetime.now(UTC) - timedelta(seconds=1)
        identity = uuid4()
        context = AcquisitionContext(acquisition_id=identity, admitted_at=now, visibility="private",
                                     policy=EffectivePolicySnapshot.model_validate(policy_snapshot()))
        page = AcquisitionResult(
            url="https://example.com/", success=True, duration_seconds=0.1,
            html="<p>Captured</p>", status_code=200,
            steps=(AcquisitionStepEvidence(attempt_number=1, step_ordinal=1, method="scroll",
                config_hash="config", config_json={"maximum_iterations": 3}, started_at=now,
                duration_ms=50, iterations=3, stop_reason="maximum_iterations",
                before_element_count=4, after_element_count=8, before_text_chars=10, after_text_chars=40,
                before_link_count=1, after_link_count=3, before_scroll_height=100, after_scroll_height=400),),
            attempt_evidence=AcquisitionAttemptEvidence(
                attempt=1, started_at=now, completed_at=now + timedelta(milliseconds=100),
                requested_url="https://example.com/", final_url="https://example.com/",
                status_code=200, outcome="success",
            ),
        )
        pipeline = SimpleNamespace(store_html=AsyncMock(return_value=SimpleNamespace(
            sha256="a" * 64, size_bytes=15, object_key="html/aa/body.zst",
            compression="zstd", compressed_size_bytes=12,
        )), enqueue_visit=AsyncMock())
        with patch("periplus.crawl.acquisition.service.capture_page", AsyncMock(return_value=page)):
            result = await acquire_page(url=page.url, context=context, browser=object(),
                                        repository_pipeline=pipeline)
        self.assertEqual(result.evidence.visit.visit_id, identity)
        self.assertEqual(result.evidence.visit.visibility, "private")
        self.assertNotIn("crawl_id", result.evidence.visit.model_dump())
        self.assertEqual(result.evidence.document.content_sha256, "a" * 64)
        self.assertIsNone(result.html)
        step, = result.evidence.steps
        self.assertEqual(step.stopping_reason, "maximum_iterations")
        self.assertEqual(step.parameters["measurements"], {"iterations": 3,
            "before": {"elements": 4, "text_chars": 10, "links": 1, "scroll_height": 100},
            "after": {"elements": 8, "text_chars": 40, "links": 3, "scroll_height": 400}})
        pipeline.store_html.assert_awaited_once()
        pipeline.enqueue_visit.assert_not_awaited()

    async def test_reserved_domain_start_does_not_wait_again(self):
        from test_frontier_capture import lease
        context = AcquisitionContext(acquisition_id=uuid4(), admitted_at=datetime.now(UTC),
            policy=EffectivePolicySnapshot.model_validate(policy_snapshot()),
            attempt_reserved_ms=6000, dispatch_policy_version=1)
        # Stop at the capture boundary: the test verifies pacing order, not content handling.
        with patch("periplus.crawl.acquisition.service.capture_page", AsyncMock(side_effect=RuntimeError("capture reached"))):
            with self.assertRaisesRegex(RuntimeError, "capture reached"):
                await acquire_page(url="https://example.com/", context=context, browser=object(),
                                   domain_pacing=object(), domain_permit=lease(), domain_start_reserved=True)
        with self.assertRaisesRegex(ValueError, "held permit"):
            await acquire_page(url="https://example.com/", context=context, browser=object(),
                               domain_start_reserved=True)

    async def test_frozen_timeout_bounds_page_creation_and_records_retry_usage(self):
        import asyncio
        from periplus.crawl.acquisition.errors import RetryableAcquisitionFailure
        async def hang(*args, **kwargs):
            await asyncio.Event().wait()
        context = AcquisitionContext(
            acquisition_id=uuid4(), admitted_at=datetime.now(UTC),
            policy=EffectivePolicySnapshot.model_validate(policy_snapshot()),
            attempt_reserved_ms=6000, dispatch_policy_version=7,
        )
        browser = SimpleNamespace(new_page=hang)
        with self.assertRaises(RetryableAcquisitionFailure) as caught:
            await acquire_page(url="https://example.com/", context=context, browser=browser,
                               persist_retryable_failure=False)
        usage = caught.exception.result.attempt_evidence.resource_usage
        self.assertEqual(usage.reserved_ms, 6000)
        self.assertEqual(usage.policy_version, 7)
        self.assertGreaterEqual(usage.measured_ms, 1000)
        self.assertEqual(caught.exception.result.failure_code, "acquisition_timeout")
