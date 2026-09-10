"""Scoped queue eligibility and durable progress do not promise dispatch times."""
from datetime import UTC, datetime, timedelta
import unittest

import test_frontier_store as fixtures
from periplus.crawl.control.collections.models import CollectionRecord
from periplus.crawl.runtime.collection_queue import _aware
from periplus.crawl.runtime.frontier_health import CrawlerActivity
from periplus.crawl.runtime.frontier_models import AcquisitionRecord, FrontierControlRecord
from periplus.crawl.runtime.frontier_views import collection_views
from periplus.crawl.runtime.selection_contract import SelectionCheckpoint


class CollectionQueueTests(unittest.TestCase):
    setUp = fixtures.FrontierStoreTests.setUp
    tearDown = fixtures.FrontierStoreTests.tearDown
    collection = fixtures.FrontierStoreTests.collection

    def view(self, identity, *, ready=True):
        now = datetime.now(UTC)
        workers = CrawlerActivity(as_of=now, state='observed', reported_workers=1,
                                 ready_workers=int(ready))
        return collection_views(self.sessions, identity=identity, workers=workers)[0]

    def test_scoped_counts_partition_queue_and_preserve_oldest_wait(self):
        now = datetime.now(UTC) - timedelta(seconds=20)
        identity = self.collection()
        first = self.store.admit(identity, 'https://example.com/a', self.context, self.policy, now=now)
        second = self.store.admit(identity, 'https://example.com/b', self.context, self.policy, now=now)
        private = self.collection(request_class='admin')
        self.store.admit(private, 'https://private.example/', self.context, self.policy, now=now)
        with self.sessions.begin() as session:
            control = session.get(FrontierControlRecord, 1)
            acquisition = session.get(AcquisitionRecord, second.acquisition_id)
            acquisition.eligible_at = datetime.now(UTC) + timedelta(minutes=1)
            acquisition.defer_reason = 'retry_backoff'
        view = self.view(identity)
        self.assertEqual(view.queued_pages, 2)
        self.assertEqual((view.queue.runnable_pages, view.queue.deferred_pages, view.queue.unknown_pages), (1, 1, 0))
        self.assertEqual(view.queue.oldest_admitted_at, now)
        self.assertGreaterEqual(view.queue.oldest_wait_seconds, 20)
        self.assertEqual([(c.reason, c.pages) for c in view.queue.constraints], [('retry_backoff', 1)])
        unknown = self.view(identity, ready=False)
        self.assertEqual((unknown.queue.runnable_pages, unknown.queue.deferred_pages, unknown.queue.unknown_pages), (0, 1, 1))
        self.store.set_collection_paused(identity, True)
        paused = self.view(identity)
        self.assertEqual(paused.queue.deferred_pages, 2)
        self.assertEqual(paused.queue.constraints[0].reason, 'collection_paused')
        self.assertNotIn('private.example', paused.model_dump_json())
        self.assertIsNotNone(first.interest_id)

    def test_only_actual_progress_advances_timestamp(self):
        identity = self.collection()
        old = datetime.now(UTC) - timedelta(minutes=2)
        with self.sessions.begin() as session:
            record = session.get(CollectionRecord, identity)
            record.last_progress_at = record.created_at = old
        self.view(identity)
        self.store.set_waiting_reason(identity, 'frontier_capacity')
        self.store.set_collection_priority(identity, 1)
        self.store.set_collection_paused(identity, True)
        self.store.set_collection_paused(identity, False)
        self.assertEqual(self.view(identity).last_progress_at, old)
        checkpoint = SelectionCheckpoint(urls=('https://example.com/',), selected_at=datetime.now(UTC))
        self.store.freeze_selection(identity, checkpoint, seeds=True)
        frozen = _aware(self.store.get_collection(identity).last_progress_at)
        self.assertGreater(frozen, old)
        self.store.freeze_selection(identity, checkpoint, seeds=True)
        self.assertEqual(_aware(self.store.get_collection(identity).last_progress_at), frozen)
        now = datetime.now(UTC)
        self.store.admit(identity, 'https://example.com/', self.context, self.policy, now=now)
        self.store.admit(identity, 'https://example.com/', self.context, self.policy, now=now + timedelta(seconds=20))
        self.assertEqual(_aware(self.store.get_collection(identity).last_progress_at), now)
        self.assertTrue(self.store.advance_selection(identity, 0, seeds=True))
        advanced = self.view(identity).last_progress_at
        self.assertFalse(self.store.advance_selection(identity, 0, seeds=True))
        self.assertEqual(self.view(identity).last_progress_at, advanced)
