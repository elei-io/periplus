"""Current drilldown bounds and visibility in both directions."""
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from frontier_fixtures import policy_snapshot
from test_frontier_store import TABLES
from periplus.crawl.control.collections.schemas import CollectionSpec, SelectionContext
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from periplus.crawl.control.domain_policies.service import ensure_default_domain_policy
from periplus.crawl.runtime.frontier_items import acquisition_view, collection_items
from periplus.crawl.runtime.frontier_models import AcquisitionRecord, FrontierControlRecord, InterestRecord
from periplus.crawl.runtime.frontier_store import FrontierStore


class FrontierItemTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        for table in TABLES:
            table.create(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(FrontierControlRecord(id=1))
            ensure_default_domain_policy(session)
        self.store = FrontierStore(self.sessions)
        self.policy = EffectivePolicySnapshot.model_validate(policy_snapshot())

    def collection(self, *, private=False):
        identity = uuid4()
        self.store.create_collection(identity, CollectionSpec(visibility="private" if private else "public"))
        return identity

    def admit(self, identity, url="https://example.com/"):
        self.store.admit(identity, url, SelectionContext(depth=0, rule_id="seed"), self.policy)
        return collection_items(self.sessions, identity, public_only=False).items[0].acquisition.id

    def test_shared_work_has_bounded_bidirectional_public_provenance(self):
        identities = [self.collection() for _ in range(12)]
        acquisition = self.admit(identities[0])
        for identity in identities[1:]:
            self.admit(identity)
        view = acquisition_view(self.sessions, acquisition)
        self.assertEqual(len(view.callers), 10)
        self.assertTrue(view.more_callers)
        for identity in identities:
            item = collection_items(self.sessions, identity).items[0]
            self.assertEqual(item.acquisition.id, acquisition)
            self.assertEqual(item.context.rule_id, "seed")
            self.assertEqual(item.budget_state, "reserved")
            self.assertIsNone(item.acquisition.next_start_estimate)

    def test_private_work_is_invisible_and_never_changes_public_caller_overflow(self):
        public, private = self.collection(), self.collection(private=True)
        acquisition = self.admit(public)
        private_acquisition = self.admit(private)
        self.assertIsNone(collection_items(self.sessions, private))
        self.assertIsNone(acquisition_view(self.sessions, private_acquisition))
        # Defensive visibility filtering survives even inconsistent association data.
        with self.sessions.begin() as session:
            record = session.query(InterestRecord).filter_by(collection_id=private).one()
            record.acquisition_id = acquisition
        view = acquisition_view(self.sessions, acquisition)
        self.assertEqual([item.collection_id for item in view.callers], [public])
        self.assertFalse(view.more_callers)
        self.assertEqual(len(acquisition_view(self.sessions, acquisition, public_only=False).callers), 2)

    def test_uuid_pages_are_bounded_and_do_not_claim_dispatch_order(self):
        identity = self.collection()
        for index in range(3):
            self.admit(identity, f"https://example.com/{index}")
        first = collection_items(self.sessions, identity, limit=2)
        second = collection_items(self.sessions, identity, limit=2, after=first.next_after)
        self.assertEqual(len(first.items), 2)
        self.assertEqual(len(second.items), 1)
        self.assertIsNone(second.next_after)
        self.assertEqual(len({item.interest_id for item in first.items + second.items}), 3)
        self.assertEqual(first.ordering, "interest_identity_not_dispatch_order")
        with self.assertRaises(ValueError):
            collection_items(self.sessions, identity, limit=101)

    def test_retry_floor_pause_and_terminal_evidence_are_distinct_from_readiness(self):
        identity = self.collection()
        acquisition = self.admit(identity)
        later = datetime.now(UTC) + timedelta(minutes=5)
        with self.sessions.begin() as session:
            record = session.get(AcquisitionRecord, acquisition)
            record.eligible_at = later
        view = acquisition_view(self.sessions, acquisition)
        self.assertEqual(view.waiting_reason, "retry_backoff")
        self.assertIsNone(view.observation_id)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).paused = True
        self.assertEqual(acquisition_view(self.sessions, acquisition).waiting_reason, "crawler_paused")
        with self.sessions.begin() as session:
            record = session.get(AcquisitionRecord, acquisition)
            record.status = "succeeded"
            record.outcome = {"visit": {"visit_id": str(acquisition)}, "secret": "must never be returned"}
            record.evidence_snapshot = 3
        view = acquisition_view(self.sessions, acquisition)
        self.assertEqual(view.observation_id, acquisition)
        self.assertTrue(view.evidence_committed)
        self.assertIsNone(view.query_ready)
        self.assertNotIn("secret", view.model_dump_json())
        self.assertIsNone(view.eligibility_not_before)

    def test_current_domain_pause_and_versioned_pacing_explain_wait_without_start_promise(self):
        identity = self.collection()
        acquisition = self.admit(identity)
        later = datetime.now(UTC) + timedelta(minutes=5)
        with self.sessions.begin() as session:
            policy = ensure_default_domain_policy(session)
            policy.paused = True
        view = acquisition_view(self.sessions, acquisition)
        self.assertEqual(view.waiting_reason, "domain_paused")
        self.assertEqual(view.estimate_unavailable_reason, "domain_paused")
        self.assertIsNone(view.next_start_estimate)
        with self.sessions.begin() as session:
            policy = ensure_default_domain_policy(session)
            policy.paused = False
            record = session.get(AcquisitionRecord, acquisition)
            record.domain_policy_id, record.domain_policy_version = policy.id, policy.version
            record.domain_eligible_at = later
        view = collection_items(self.sessions, identity).items[0].acquisition
        self.assertEqual(view.waiting_reason, "domain_pacing")
        self.assertEqual(view.eligibility_not_before, later)
        with self.sessions.begin() as session:
            ensure_default_domain_policy(session).version += 1
        view = acquisition_view(self.sessions, acquisition)
        self.assertEqual(view.waiting_reason, "awaiting_scheduler_evaluation")
        self.assertLess(view.eligibility_not_before, later)

    def test_known_rate_and_allowance_constraints_are_not_generic_scheduler_waits(self):
        identity = self.collection()
        acquisition = self.admit(identity)
        later = datetime.now(UTC) + timedelta(minutes=2)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).next_dispatch_at = later
        view = acquisition_view(self.sessions, acquisition)
        self.assertEqual(view.waiting_reason, "global_pacing")
        self.assertEqual(view.eligibility_not_before, later)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).attempt_allowance = 0
        view = acquisition_view(self.sessions, acquisition)
        self.assertEqual(view.waiting_reason, "attempt_allowance_exhausted")
        self.assertIsNone(view.next_start_estimate)

    def test_capacity_reason_does_not_expose_other_callers_or_counts(self):
        identity = self.collection()
        acquisition = self.admit(identity)
        private = self.collection(private=True)
        occupied = self.admit(private)
        with self.sessions.begin() as session:
            ensure_default_domain_policy(session).maximum_concurrency = 1
        self.assertIsNotNone(self.store.dispatch(occupied))
        view = acquisition_view(self.sessions, acquisition)
        self.assertEqual(view.waiting_reason, "domain_capacity")
        self.assertEqual([caller.collection_id for caller in view.callers], [identity])
        self.assertNotIn(str(private), view.model_dump_json())
        self.assertNotIn(str(occupied), view.model_dump_json())
        with self.sessions.begin() as session:
            ensure_default_domain_policy(session).maximum_concurrency = 4
            session.get(FrontierControlRecord, 1).dispatch_limit = 1
        self.assertEqual(acquisition_view(self.sessions, acquisition).waiting_reason, "dispatch_capacity")

    def test_start_range_requires_recent_comparable_public_samples_and_ready_worker(self):
        from periplus.crawl.runtime.frontier_health import CrawlerActivity
        identity = self.collection()
        acquisition = self.admit(identity)
        now = datetime.now(UTC)
        workers = CrawlerActivity(as_of=now, state='observed', ready_workers=1)
        with self.sessions.begin() as session:
            policy = ensure_default_domain_policy(session)
            control = session.get(FrontierControlRecord, 1)
            for index, seconds in enumerate((10, 20, 30)):
                start = now - timedelta(seconds=20 + index)
                session.add(AcquisitionRecord(url=f'https://example.com/sample-{index}', domain='example.com',
                    capture_key=str(uuid4()), visibility='public', access_context='public', status='succeeded',
                    requirements=self.policy.model_dump(mode='json'), created_at=start - timedelta(seconds=seconds),
                    completed_at=start + timedelta(seconds=2), attempt_count=1, dispatch_policy_version=control.policy_version,
                    attempt_domain_policy={'id':str(policy.id),'version':policy.version},
                    outcome={'attempts':[{'started_at':start.isoformat()}]}))
        view = acquisition_view(self.sessions, acquisition, workers=workers)
        self.assertIsNotNone(view.next_start_estimate)
        self.assertIsNone(view.estimate_unavailable_reason)
        estimate = view.next_start_estimate
        self.assertEqual(estimate.sample_size, 3)
        self.assertGreater(estimate.latest_at, estimate.earliest_at)
        self.assertGreaterEqual(estimate.earliest_at, estimate.calculated_at)
        self.assertEqual(estimate.uncertainty, 'conditional_not_a_guarantee')
        self.assertIsNotNone(collection_items(self.sessions, identity, workers=workers).items[0].acquisition.next_start_estimate)
        stale = workers.model_copy(update={'as_of': now - timedelta(seconds=10)})
        self.assertEqual(acquisition_view(self.sessions, acquisition, workers=stale).estimate_unavailable_reason,
            'worker_readiness_not_observed')
        competing = self.collection(private=True)
        self.admit(competing)
        crowded = acquisition_view(self.sessions, acquisition, workers=workers)
        self.assertIsNone(crowded.next_start_estimate)
        self.assertNotIn(str(competing), crowded.model_dump_json())
        self.store.stop_collection(competing)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).policy_version += 1
        self.assertEqual(acquisition_view(self.sessions, acquisition, workers=workers).estimate_unavailable_reason,
            'insufficient_comparable_starts')

    def test_observed_range_does_not_invent_a_start_after_samples_are_exceeded(self):
        from periplus.crawl.runtime.start_estimates import observed_range
        now = datetime.now(UTC)
        self.assertIsNone(observed_range([10,20,30], admitted_at=now-timedelta(seconds=40), eligible_at=now, now=now))
        self.assertIsNone(observed_range([10,20,30], admitted_at=now, eligible_at=now+timedelta(seconds=40), now=now))
        self.assertIsNone(observed_range([10,20], admitted_at=now, eligible_at=now, now=now))
