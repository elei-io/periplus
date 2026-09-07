import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from periplus.crawl.runtime.frontier_health import CrawlerPresenceReader, DispatchHealth, DispatchReadiness
from periplus.crawl.runtime.frontier_queue import CrawlerPresence


class FrontierHealthTests(unittest.IsolatedAsyncioTestCase):
    def entry(self, report, *, age=0):
        now = datetime.now(UTC)
        data = CrawlerPresence(worker_id='private-host-name', started_at=now, last_seen_at=now,
                               capture_lanes=2, dispatch=report).model_dump_json().encode()
        return SimpleNamespace(data=data, time=now - timedelta(seconds=age))

    async def test_recent_health_aggregates_exclude_stale_reports_and_host_identity(self):
        ready = DispatchHealth()
        ready.ready()
        blocked = DispatchHealth()
        blocked.blocked('storage_unavailable')
        entries = {'ready': self.entry(ready.snapshot()), 'blocked': self.entry(blocked.snapshot()),
                   'checking': self.entry(DispatchReadiness(state='checking', checked_at=datetime.now(UTC))),
                   'stale': self.entry(ready.snapshot(), age=20)}
        bucket = SimpleNamespace(stream_info=AsyncMock(return_value=SimpleNamespace(state=SimpleNamespace(subjects=dict.fromkeys(entries, 1)))), get_msg=AsyncMock(side_effect=lambda stream, subject: entries[subject]))
        reader = CrawlerPresenceReader(bucket)
        result = await reader.read()
        self.assertEqual((result.reported_workers, result.ready_workers, result.blocked_workers,
                          result.checking_workers, result.excluded_reports), (3, 1, 1, 1, 1))
        self.assertEqual(result.waiting_reasons, ['storage_unavailable'])
        self.assertNotIn('private-host-name', result.model_dump_json())
        self.assertIs(await reader.read(), result)
        bucket.stream_info.assert_awaited_once()

    async def test_expired_check_is_unknown_even_with_a_fresh_heartbeat(self):
        now = datetime.now(UTC)
        old = DispatchReadiness(state='ready', checked_at=now-timedelta(seconds=10), valid_until=now-timedelta(seconds=5))
        bucket = SimpleNamespace(stream_info=AsyncMock(return_value=SimpleNamespace(state=SimpleNamespace(subjects={'a': 1}))), get_msg=AsyncMock(return_value=self.entry(old)))
        result = await CrawlerPresenceReader(bucket).read()
        self.assertEqual((result.ready_workers, result.unknown_workers), (0, 1))

    async def test_empty_and_failed_presence_are_not_stopped_or_healthy_claims(self):
        bucket = SimpleNamespace(stream_info=AsyncMock(return_value=SimpleNamespace(state=SimpleNamespace(subjects={}))))
        result = await CrawlerPresenceReader(bucket).read()
        self.assertEqual(result.state, 'no_recent_reports')
        self.assertEqual(result.reason, 'worker_availability_unknown')
        bucket.stream_info.side_effect = OSError('credential-secret')
        result = await CrawlerPresenceReader(bucket).read()
        self.assertEqual(result.state, 'unavailable')
        self.assertIsNone(result.ready_workers)
        self.assertNotIn('credential-secret', result.model_dump_json())

    async def test_preview_decodes_at_most_128_reports_and_discloses_truncation(self):
        bucket = SimpleNamespace(stream_info=AsyncMock(return_value=SimpleNamespace(state=SimpleNamespace(subjects={str(i):1 for i in range(140)}))),
            get_msg=AsyncMock(return_value=self.entry(DispatchReadiness(state='checking', checked_at=datetime.now(UTC)))))
        result = await CrawlerPresenceReader(bucket).read()
        self.assertTrue(result.more_workers)
        self.assertEqual(result.reported_workers, 128)
        self.assertEqual(bucket.get_msg.await_count, 128)

    async def test_simultaneous_readers_share_one_remote_read(self):
        bucket = SimpleNamespace(stream_info=AsyncMock(return_value=SimpleNamespace(state=SimpleNamespace(subjects={}))))
        reader = CrawlerPresenceReader(bucket)
        await asyncio.gather(*(reader.read() for _ in range(10)))
        bucket.stream_info.assert_awaited_once()

    async def test_worker_heartbeat_publishes_dispatch_health(self):
        from periplus.crawl.crawler import _presence
        from unittest.mock import MagicMock
        health = DispatchHealth()
        health.blocked('ingestion_delivery_unavailable')
        stop = asyncio.Event()
        bucket = SimpleNamespace(put=AsyncMock(side_effect=lambda *args: stop.set()))
        info = SimpleNamespace(num_pending=0, num_ack_pending=0, ack_floor=SimpleNamespace(stream_seq=0))
        jetstream = SimpleNamespace(consumer_info=AsyncMock(return_value=info))
        await _presence(bucket, jetstream, 'worker:example', datetime.now(UTC), MagicMock(), stop, health)
        report = CrawlerPresence.model_validate_json(bucket.put.call_args.args[1])
        self.assertEqual(report.dispatch.state, 'blocked')
        self.assertEqual(report.dispatch.reason, 'ingestion_delivery_unavailable')

    async def test_invalid_and_future_reports_are_excluded(self):
        now = datetime.now(UTC)
        entries = [SimpleNamespace(data=b'invalid secret', time=now),
                   self.entry(DispatchReadiness(state='checking', checked_at=datetime.now(UTC)), age=-60)]
        bucket = SimpleNamespace(stream_info=AsyncMock(return_value=SimpleNamespace(state=SimpleNamespace(subjects={'a':1, 'b':1}))), get_msg=AsyncMock(side_effect=entries))
        result = await CrawlerPresenceReader(bucket).read()
        self.assertEqual(result.state, 'no_recent_reports')
        self.assertEqual(result.excluded_reports, 2)
        self.assertNotIn('secret', result.model_dump_json())

    def test_dispatch_health_expires_without_a_new_dispatch_loop_update(self):
        health = DispatchHealth()
        self.assertEqual(health.snapshot().state, 'unknown')
        health.ready()
        with patch('periplus.crawl.runtime.frontier_health.time.monotonic', return_value=health.valid_until + 1):
            self.assertEqual(health.snapshot().state, 'unknown')
        health.blocked('cdp_unavailable')
        self.assertEqual(health.snapshot().reason, 'cdp_unavailable')
