"""Capture handlers ACK only after frontier acceptance, never direct ingestion."""
import asyncio
from test_domain_policies import FakeBucket
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from frontier_fixtures import policy_snapshot
from periplus.crawl.acquisition.models import AcquisitionResult
from periplus.crawl.runtime.frontier_capture import handle_capture_delivery, acquisition_context
from periplus.crawl.runtime.frontier_queue import CaptureWork
from periplus.platform.catalogue.records import AttemptRecord, VisitEvidence, VisitRecord, attempt_id_for


@asynccontextmanager
async def lease(*args, **kwargs):
    yield SimpleNamespace(lost=False, wait_lost=asyncio.Event().wait)


class FrontierCaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        connection = patch("periplus.crawl.runtime.frontier_capture.connect_cdp", lease)
        connection.start()
        self.addCleanup(connection.stop)
        probe = patch("periplus.crawl.runtime.frontier_capture.public_destination_url", AsyncMock(side_effect=lambda url: url))
        self.destination = probe.start()
        self.addCleanup(probe.stop)
        from periplus.crawl.runtime.frontier_capture import _active_captures
        self.active_metric = _active_captures
        self.addCleanup(lambda: self.assertEqual(self.active_metric._value.get(), 0))

    async def test_authorized_capture_occupancy_clears_after_cancellation(self):
        acquisition = self.acquisition()
        store = MagicMock()
        store.get_acquisition.return_value = acquisition
        store.current_domain_policy.return_value = acquisition_context(acquisition).policy.domain
        store.begin_attempt.return_value = True
        store.needs_navigation.return_value = False
        message = AsyncMock()
        message.data = CaptureWork(acquisition_id=acquisition.id, generation=1).model_dump_json().encode()
        entered = asyncio.Event()
        async def capture(**kwargs):
            self.assertEqual(self.active_metric._value.get(), 1)
            entered.set()
            await asyncio.Event().wait()
        with patch("periplus.crawl.runtime.frontier_capture.operation_leases", lease), patch("periplus.crawl.runtime.frontier_capture.domain_permit", lease), patch("periplus.crawl.runtime.frontier_capture.acquire_page", capture):
            task = asyncio.create_task(handle_capture_delivery(message, store, AsyncMock(), object(), operation_bucket=object(), domain_bucket=FakeBucket()))
            try:
                await asyncio.wait_for(entered.wait(), 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.active_metric._value.get(), 0)

    async def test_cdp_connection_failure_defers_without_authorizing_or_pacing_a_start(self):
        from periplus.crawl.acquisition.capture import connect_cdp
        acquisition = self.acquisition()
        store = MagicMock()
        store.get_acquisition.return_value = acquisition
        store.current_domain_policy.return_value = acquisition_context(acquisition).policy.domain
        store.defer_unstarted.return_value = True
        message = AsyncMock()
        message.data = CaptureWork(acquisition_id=acquisition.id, generation=1).model_dump_json().encode()
        playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=AsyncMock(side_effect=OSError('offline'))))
        with patch("periplus.crawl.runtime.frontier_capture.operation_leases", lease), \
             patch("periplus.crawl.runtime.frontier_capture.domain_permit", lease), \
             patch("periplus.crawl.runtime.frontier_capture.connect_cdp", connect_cdp), \
             patch("periplus.crawl.runtime.frontier_capture.try_domain_start", AsyncMock()) as pace:
            await handle_capture_delivery(message, store, AsyncMock(), playwright,
                                          operation_bucket=object(), domain_bucket=FakeBucket())
        pace.assert_not_awaited()
        store.begin_attempt.assert_not_called()
        self.assertEqual(self.active_metric._value.get(), 0)
        store.defer_unstarted.assert_called_once_with(acquisition.id, 1, delay_seconds=30,
            domain_policy=None, reason='cdp_unavailable')
        message.ack.assert_awaited_once()

    async def test_storage_outage_defers_before_domain_or_physical_attempt(self):
        acquisition = self.acquisition()
        store = MagicMock()
        store.get_acquisition.return_value = acquisition
        store.defer_unstarted.return_value = True
        pipeline = AsyncMock()
        pipeline.check_storage_available.side_effect = OSError("storage offline")
        message = AsyncMock()
        message.data = CaptureWork(acquisition_id=acquisition.id, generation=1).model_dump_json().encode()
        with patch("periplus.crawl.runtime.frontier_capture.operation_leases", lease), \
             patch("periplus.crawl.runtime.frontier_capture.domain_permit") as domain:
            await handle_capture_delivery(message, store, pipeline, object(),
                operation_bucket=object(), domain_bucket=FakeBucket())
        domain.assert_not_called()
        store.begin_attempt.assert_not_called()
        store.defer_unstarted.assert_called_once_with(acquisition.id, 1, delay_seconds=30,
            domain_policy=None, reason="storage_unavailable")
        message.ack.assert_awaited_once()

    async def test_destination_failure_precedes_domain_and_attempt_authorization(self):
        from periplus.crawl.acquisition.destination import DestinationRejected, DestinationUnavailable
        for failure in (DestinationRejected('private'), DestinationUnavailable('DNS offline')):
            with self.subTest(failure=type(failure).__name__):
                acquisition = self.acquisition()
                store = MagicMock()
                store.get_acquisition.return_value = acquisition
                store.reject_destination.return_value = store.defer_unstarted.return_value = True
                message = AsyncMock()
                message.data = CaptureWork(acquisition_id=acquisition.id, generation=1).model_dump_json().encode()
                self.destination.side_effect = failure
                with patch("periplus.crawl.runtime.frontier_capture.operation_leases", lease), \
                     patch("periplus.crawl.runtime.frontier_capture.domain_permit") as domain:
                    await handle_capture_delivery(message, store, AsyncMock(), object(),
                        operation_bucket=object(), domain_bucket=FakeBucket())
                store.begin_attempt.assert_not_called()
                domain.assert_not_called()
                if isinstance(failure, DestinationRejected):
                    store.reject_destination.assert_called_once_with(acquisition.id, 1)
                    store.defer_unstarted.assert_not_called()
                else:
                    store.reject_destination.assert_not_called()
                    self.assertEqual(store.defer_unstarted.call_args.kwargs['reason'], 'destination_dns_unavailable')
                message.ack.assert_awaited_once()

    def acquisition(self):
        return SimpleNamespace(id=uuid4(), generation=1, status="dispatched", url="https://example.com/",
                                domain="example.com", requirements=policy_snapshot(), created_at=datetime.now(UTC),
                               prior_results=[], uncertain_attempts=[], attempt_count=0, attempt_limit=3,
                               attempt_reserved_ms=125000, dispatch_policy_version=1, attempt_domain_policy=None, attempt_exclusions=[], attempt_exclusion_version=1)

    async def test_accepted_evidence_precedes_ack(self):
        acquisition = self.acquisition()
        evidence = VisitEvidence(visit=VisitRecord(
            visit_id=acquisition.id,  requested_url=acquisition.url, admitted_at=acquisition.created_at,
            started_at=acquisition.created_at, finished_at=acquisition.created_at, outcome="succeeded",
        ), attempts=())
        result = AcquisitionResult(url=acquisition.url, success=True, duration_seconds=0.1, evidence=evidence)
        store = MagicMock()
        store.get_acquisition.return_value = acquisition
        store.current_domain_policy.return_value = acquisition_context(acquisition).policy.domain
        store.begin_attempt.return_value = True
        store.renew_attempt.return_value = True
        store.needs_navigation.return_value = False
        events = []
        browser = object()

        @asynccontextmanager
        async def connection(playwright):
            events.append("connected")
            try:
                yield browser
            finally:
                events.append("disconnected")

        store.begin_attempt.side_effect = lambda *args, **kwargs: events.append("started") or True
        store.complete.side_effect = lambda *args, **kwargs: events.append("accepted")
        message = AsyncMock()
        message.data = CaptureWork(acquisition_id=acquisition.id, generation=1).model_dump_json().encode()
        message.ack.side_effect = lambda: events.append("ack")
        pipeline = AsyncMock()
        with patch("periplus.crawl.runtime.frontier_capture.operation_leases", lease), \
             patch("periplus.crawl.runtime.frontier_capture.domain_permit", lease), \
             patch("periplus.crawl.runtime.frontier_capture.connect_cdp", connection), \
             patch("periplus.crawl.runtime.frontier_capture.acquire_page", AsyncMock(return_value=result)) as capture:
            await handle_capture_delivery(message, store, pipeline, object(),
                                          operation_bucket=object(), domain_bucket=FakeBucket())
        self.assertEqual(events, ["connected", "started", "accepted", "disconnected", "ack"])
        self.assertIs(capture.call_args.kwargs["browser"], browser)
        self.assertEqual(store.complete.call_args.kwargs["evidence"], evidence)
        pipeline.enqueue_visit.assert_not_awaited()
        message.nak.assert_not_awaited()

    async def test_delivery_outage_releases_unstarted_work_before_domain_or_physical_authorization(self):
        acquisition = self.acquisition()
        store = MagicMock()
        store.get_acquisition.return_value = acquisition
        store.defer_unstarted.return_value = True
        message = AsyncMock()
        message.data = CaptureWork(acquisition_id=acquisition.id, generation=1).model_dump_json().encode()
        pipeline = AsyncMock()
        pipeline.queue.check_available.side_effect = OSError('delivery unavailable')
        with patch("periplus.crawl.runtime.frontier_capture.operation_leases", lease), \
             patch("periplus.crawl.runtime.frontier_capture.domain_permit") as domain, \
             patch("periplus.crawl.runtime.frontier_capture.acquire_page", AsyncMock()) as capture:
            await handle_capture_delivery(message, store, pipeline, object(),
                                          operation_bucket=object(), domain_bucket=FakeBucket())
        store.begin_attempt.assert_not_called()
        store.current_domain_policy.assert_not_called()
        domain.assert_not_called()
        capture.assert_not_awaited()
        store.defer_unstarted.assert_called_once_with(acquisition.id, 1, delay_seconds=30, domain_policy=None, reason="ingestion_delivery_unavailable")
        message.ack.assert_awaited_once()
        message.nak.assert_not_awaited()

    async def test_domain_delay_defers_before_beginning_attempt_or_capture(self):
        from periplus.crawl.runtime.domain_pacing import record_domain_response
        acquisition = self.acquisition()
        store = MagicMock()
        store.get_acquisition.return_value = acquisition
        store.current_domain_policy.return_value = acquisition_context(acquisition).policy.domain
        store.defer_unstarted.return_value = True
        message = AsyncMock()
        message.data = CaptureWork(acquisition_id=acquisition.id, generation=1).model_dump_json().encode()
        bucket = FakeBucket()
        await record_domain_response(bucket, domain=acquisition.domain, status_code=429, retry_after_seconds=300)
        with patch("periplus.crawl.runtime.frontier_capture.operation_leases", lease), \
             patch("periplus.crawl.runtime.frontier_capture.domain_permit", lease), \
             patch("periplus.crawl.runtime.frontier_capture.acquire_page", AsyncMock()) as capture:
            await handle_capture_delivery(message, store, AsyncMock(), object(),
                                          operation_bucket=object(), domain_bucket=bucket)
        store.begin_attempt.assert_not_called()
        capture.assert_not_awaited()
        store.defer_unstarted.assert_called_once()
        self.assertGreater(store.defer_unstarted.call_args.kwargs["delay_seconds"], 299)
        message.ack.assert_awaited_once()
        message.nak.assert_not_awaited()

    async def test_superseded_delivery_never_starts_capture(self):
        acquisition = self.acquisition()
        store = MagicMock()
        store.get_acquisition.return_value = acquisition
        store.current_domain_policy.return_value = acquisition_context(acquisition).policy.domain
        message = AsyncMock()
        message.data = CaptureWork(acquisition_id=acquisition.id, generation=2).model_dump_json().encode()
        with patch("periplus.crawl.runtime.frontier_capture.acquire_page", AsyncMock()) as capture:
            await handle_capture_delivery(message, store, AsyncMock(), object(),
                                          operation_bucket=object(), domain_bucket=FakeBucket())
        capture.assert_not_awaited()
        message.ack.assert_awaited_once()

    def test_unknown_attempt_preserves_unknown_finish_time(self):
        acquisition = self.acquisition()
        acquisition.uncertain_attempts = [{"started_at": acquisition.created_at.isoformat(), "generation": 1,
                                           "resource_usage": {"policy_version": 1, "reserved_ms": 125000, "measured_ms": None}}]
        context = acquisition_context(acquisition)
        self.assertEqual(context.prior_attempts[0].outcome, "uncertain")
        self.assertIsNone(context.prior_attempts[0].completed_at)
        attempt = AttemptRecord(attempt_id=attempt_id_for(acquisition.id, 0), visit_id=acquisition.id,
                                attempt_index=0, started_at=acquisition.created_at, finished_at=None, outcome="uncertain")
        self.assertIsNone(attempt.finished_at)
