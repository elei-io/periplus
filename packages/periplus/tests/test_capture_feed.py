"""Completion cursor bounds, bootstrap, visibility, and burst continuity."""
from datetime import UTC, datetime, timedelta
import unittest
from unittest.mock import patch
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from test_frontier_store import TABLES
from periplus.crawl.runtime.capture_feed import capture_page, decode_capture_cursor
from periplus.crawl.runtime.frontier_models import AcquisitionRecord, FrontierControlRecord


class CaptureFeedTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        for table in TABLES:
            table.create(engine)
        self.sessions = sessionmaker(engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(FrontierControlRecord(id=1))
        self.now = datetime(2026, 9, 7, tzinfo=UTC)
        self.sequence = 0

    def add(self, count, *, at=None, visibility='public', status='succeeded'):
        identities = []
        with self.sessions.begin() as session:
            for _ in range(count):
                self.sequence += 1
                identity = UUID(int=self.sequence)
                identities.append(identity)
                session.add(AcquisitionRecord(id=identity, url=f'https://example.com/{identity}',
                    domain='example.com', capture_key=str(identity), requirements={}, visibility=visibility,
                    access_context='public', status=status, completed_at=at or self.now))
        return identities

    def read(self, cursor=None, *, at=None):
        with patch('periplus.crawl.runtime.capture_feed.FrontierStore._transaction_now', return_value=at or self.now):
            return capture_page(self.sessions, decode_capture_cursor(cursor))

    def test_bootstrap_is_recent_public_successes_and_idle_does_not_replay(self):
        public = self.add(12)
        self.add(8, visibility='private')
        self.add(8, status='failed')
        first = self.read()
        self.assertEqual([item.observation_id for item in first.items], public[-7:])
        self.assertTrue(first.bootstrap)
        self.assertFalse(first.has_more)
        self.assertTrue(all(item.query_ready is None for item in first.items))
        following = self.read(first.cursor, at=self.now + timedelta(seconds=5))
        self.assertEqual(following.items, [])
        self.assertFalse(following.bootstrap)
        self.assertIsNone(following.reset_reason)
        self.assertNotEqual(following.cursor, first.cursor)

    def test_burst_with_equal_timestamps_paginates_without_losing_later_arrivals(self):
        first = self.read()
        burst_time = self.now + timedelta(seconds=1)
        expected = self.add(450, at=burst_time)
        one = self.read(first.cursor, at=burst_time)
        self.assertEqual(len(one.items), 200)
        self.assertTrue(one.has_more)
        later = self.add(3, at=self.now + timedelta(seconds=2))
        two = self.read(one.cursor, at=self.now + timedelta(seconds=3))
        three = self.read(two.cursor, at=self.now + timedelta(seconds=4))
        self.assertEqual([item.observation_id for page in (one, two, three) for item in page.items], expected)
        self.assertFalse(three.has_more)
        four = self.read(three.cursor, at=self.now + timedelta(seconds=5))
        self.assertEqual([item.observation_id for item in four.items], later)
        # Retrying a page is deterministic even as new captures finish.
        retry = self.read(one.cursor, at=self.now + timedelta(seconds=6))
        self.assertEqual([item.observation_id for item in retry.items], [item.observation_id for item in two.items])

    def test_expired_cursor_explicitly_resyncs_and_invalid_cursor_is_rejected(self):
        first = self.read()
        self.add(10, at=self.now + timedelta(minutes=11))
        reset = self.read(first.cursor, at=self.now + timedelta(minutes=11))
        self.assertTrue(reset.bootstrap)
        self.assertEqual(reset.reset_reason, 'cursor_expired')
        self.assertEqual(len(reset.items), 7)
        for value in ('not a cursor', 'x' * 1025, 'e30'):
            with self.assertRaises(ValueError):
                decode_capture_cursor(value)
