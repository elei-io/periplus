"""First-admission observations are transactional, bounded and visibility scoped."""
from datetime import UTC, datetime, timedelta
import unittest
from uuid import uuid4

import test_frontier_store as fixtures
from periplus.crawl.control.collections.models import CollectionRecord
from periplus.crawl.control.collections.schemas import CollectionSpec
from periplus.crawl.runtime.frontier_health import CrawlerActivity
from periplus.crawl.runtime.frontier_models import FrontierControlRecord
from periplus.crawl.runtime.frontier_views import collection_views


class AdmissionEstimateTests(unittest.TestCase):
    setUp = fixtures.FrontierStoreTests.setUp
    tearDown = fixtures.FrontierStoreTests.tearDown

    def request(self, *, created_at, visibility='public'):
        identity = uuid4()
        self.store.create_collection(identity, CollectionSpec(seed_urls=('https://example.com/',),
            result_max_age_seconds=0, visibility=visibility))
        with self.sessions.begin() as session:
            record = session.get(CollectionRecord, identity)
            record.created_at = record.service_after = created_at
        return identity

    def samples(self, now, *, visibility='public'):
        for seconds in (10, 20, 30):
            identity = self.request(created_at=now - timedelta(seconds=seconds + 20), visibility=visibility)
            self.store.admit(identity, 'https://example.com/', self.context, self.policy,
                             now=now - timedelta(seconds=20))

    def test_recent_same_domain_admissions_support_a_range_but_capacity_and_changed_controls_do_not(self):
        now = datetime.now(UTC)
        self.samples(now)
        identity = self.request(created_at=now - timedelta(seconds=1))
        workers = CrawlerActivity(as_of=now, state='observed', reported_workers=1, ready_workers=1)
        view, = collection_views(self.sessions, identity=identity, workers=workers)
        estimate = view.admission.estimate
        self.assertIsNotNone(estimate)
        self.assertEqual(estimate.scope, 'first_admission')
        self.assertEqual(estimate.sample_size, 3)
        self.assertGreater(estimate.latest_at, estimate.earliest_at)
        self.assertIsNone(view.admission.estimate_unavailable_reason)
        blocked = workers.model_copy(update={'ready_workers': 0, 'blocked_workers': 1,
                                             'waiting_reasons': ['cdp_unavailable']})
        view, = collection_views(self.sessions, identity=identity, workers=blocked)
        self.assertIsNotNone(view.admission.estimate)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).admission_limit = 1
        view, = collection_views(self.sessions, identity=identity, workers=workers)
        self.assertIsNone(view.admission.estimate)
        self.assertEqual(view.admission.estimate_unavailable_reason, 'frontier_capacity_limits_estimation')
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).policy_version += 1
        view, = collection_views(self.sessions, identity=identity, workers=workers)
        self.assertEqual(view.admission.estimate_unavailable_reason, 'admission_controls_changed')

    def test_private_samples_cannot_support_public_admission_predictions(self):
        now = datetime.now(UTC)
        self.samples(now, visibility='private')
        identity = self.request(created_at=now)
        workers = CrawlerActivity(as_of=now, state='observed', reported_workers=1, ready_workers=1)
        view, = collection_views(self.sessions, identity=identity, workers=workers)
        self.assertIsNone(view.admission.estimate)
        self.assertEqual(view.admission.estimate_unavailable_reason, 'insufficient_comparable_admissions')

    def test_first_admission_timing_survives_retries_and_priority_changes_invalidate_comparability(self):
        now = datetime.now(UTC)
        identity = self.request(created_at=now - timedelta(seconds=10))
        first = self.store.admit(identity, 'https://example.com/', self.context, self.policy, now=now)
        replay = self.store.admit(identity, 'https://example.com/', self.context, self.policy,
                                  now=now + timedelta(seconds=20))
        self.assertEqual(first.interest_id, replay.interest_id)
        self.assertEqual(self.store.get_collection(identity).admission_timing['first_admitted_at'], now.isoformat())
        self.store.set_collection_priority(identity, 1)
        self.assertIsNone(self.store.get_collection(identity).admission_timing)
