"""Behavioral checks for shared acquisition and once-per-request accounting."""
from datetime import UTC, datetime, timedelta
import unittest
from uuid import uuid4

from periplus.crawl.control.domain_policies.service import ensure_default_domain_policy

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from frontier_fixtures import navigation_package, policy_snapshot
from periplus.platform.catalogue.records import AttemptRecord, AttemptUsage, attempt_id_for
from periplus.crawl.control.collections.models import CollectionRecord
from periplus.crawl.control.collections.schemas import CollectionSpec, SelectionContext
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from periplus.crawl.runtime.frontier_models import (
    AcquisitionRecord, BackgroundCheckRecord, FrontierControlRecord, FrontierOutboxRecord, InterestRecord,
)
from periplus.crawl.runtime.frontier_store import (
    AdmissionDeferred, CollectionUnavailable, FrontierStore, StaleDispatch,
)

from periplus.crawl.control.domain_policies.models import DomainPolicy

TABLES = (DomainPolicy.__table__, CollectionRecord.__table__, FrontierControlRecord.__table__,
          AcquisitionRecord.__table__, InterestRecord.__table__, FrontierOutboxRecord.__table__, BackgroundCheckRecord.__table__)


class FrontierStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        for table in TABLES:
            table.create(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(FrontierControlRecord(id=1, captures_per_minute=0))
            ensure_default_domain_policy(session)
        self.store = FrontierStore(self.sessions)
        self.policy = EffectivePolicySnapshot.model_validate(policy_snapshot())
        self.now = datetime(2026, 9, 7, tzinfo=UTC)
        self.context = SelectionContext(depth=0, rule_id="seeds")

    def tearDown(self):
        self.engine.dispose()

    def test_admission_view_counts_remaining_seed_and_follow_candidates_without_cross_request_leaks(self):
        from periplus.crawl.runtime.frontier_views import collection_views
        from periplus.crawl.runtime.selection_contract import SelectionCheckpoint
        identity = self.collection()
        admission = self.store.admit(identity, 'https://parent.example/', self.context, self.policy, now=self.now)
        with self.sessions.begin() as session:
            collection = session.get(CollectionRecord, identity)
            collection.selection_checkpoint = SelectionCheckpoint(urls=(
                'https://done.example/', 'https://seed.example/'), cursor=1,
                selected_at=self.now).model_dump(mode='json')
            collection.waiting_reason = 'frontier_capacity'
            parent = session.get(InterestRecord, admission.interest_id)
            parent.status = 'selecting'
            parent.selection_checkpoint = SelectionCheckpoint(urls=(
                'https://follow.example/', 'https://other.example/'),
                selected_at=self.now + timedelta(seconds=10)).model_dump(mode='json')
        private = uuid4()
        self.store.create_collection(private, CollectionSpec(seed_urls=('https://private.example/',),
                                                              visibility='private'))
        self.store.freeze_selection(private, SelectionCheckpoint(urls=('https://private.example/',),
                                                                  selected_at=self.now), seeds=True)
        view, = collection_views(self.sessions, identity=identity)
        self.assertEqual(view.admission.pending_candidates, 3)
        self.assertEqual(view.admission.preview_urls, ('https://seed.example/', 'https://follow.example/'))
        self.assertEqual(view.admission.oldest_selected_at, self.now)
        self.assertGreaterEqual(view.admission.elapsed_seconds, 0)
        self.assertEqual(view.admission.estimate_unavailable_reason, 'frontier_capacity')
        self.assertNotIn('private.example', view.model_dump_json())

    def test_unresolved_selection_is_not_reported_as_admitted_or_timed_wait(self):
        from periplus.crawl.runtime.frontier_views import collection_views
        identity = self.collection()
        view, = collection_views(self.sessions, identity=identity)
        self.assertEqual(view.admission.pending_candidates, 0)
        self.assertEqual(view.admission.preview_urls, ())
        self.assertIsNone(view.admission.elapsed_seconds)
        self.assertEqual(view.admission.estimate_unavailable_reason, 'comparable_selection_work_not_observed')

    def test_repeated_delivery_deferral_does_not_spend_physical_budget_or_hide_its_reason(self):
        from periplus.crawl.runtime.frontier_items import acquisition_view
        identity = self.collection()
        admission = self.store.admit(identity, 'https://example.com/', self.context, self.policy, now=self.now)
        now = datetime.now(UTC)
        for _ in range(3):
            work = self.store.dispatch(admission.acquisition_id, now=now)
            self.assertIsNotNone(work)
            self.assertTrue(self.store.defer_unstarted(work.acquisition_id, work.generation,
                delay_seconds=30, now=now, reason='ingestion_delivery_unavailable'))
            self.assertEqual(acquisition_view(self.sessions, work.acquisition_id).waiting_reason,
                             'ingestion_delivery_unavailable')
            with self.sessions() as session:
                control = session.get(FrontierControlRecord, 1)
                self.assertEqual((control.started_attempts, control.charged_capture_ms,
                                  control.reserved_attempts, control.reserved_capture_ms, control.active_count),
                                 (0, 0, 0, 0, 0))
            now += timedelta(seconds=31)
        self.assertEqual(self.store.get_collection(identity).consumed, 1)
        work = self.store.dispatch(admission.acquisition_id, now=now)
        self.assertTrue(self.store.begin_attempt(work.acquisition_id, work.generation, now=now))
        self.assertIsNone(self.store.get_acquisition(work.acquisition_id).defer_reason)

    def test_private_destination_rejection_is_fenced_and_releases_physical_allowance(self):
        from periplus.crawl.runtime.frontier_items import acquisition_view
        identity = self.collection()
        admission = self.store.admit(identity, 'http://127.0.0.1/', self.context, self.policy, now=self.now)
        work = self.store.dispatch(admission.acquisition_id, now=self.now)
        self.assertFalse(self.store.reject_destination(work.acquisition_id, work.generation + 1))
        self.assertTrue(self.store.reject_destination(work.acquisition_id, work.generation))
        self.assertFalse(self.store.reject_destination(work.acquisition_id, work.generation))
        value = acquisition_view(self.sessions, work.acquisition_id)
        self.assertEqual(value.status, 'cancelled')
        self.assertEqual(value.terminal_reason, 'non_public_destination')
        self.assertIsNone(value.observation_id)
        with self.sessions() as session:
            control = session.get(FrontierControlRecord, 1)
            self.assertEqual((control.started_attempts, control.charged_capture_ms,
                              control.reserved_attempts, control.reserved_capture_ms, control.active_count), (0,0,0,0,0))

    def collection(self, **kwargs):
        identity = uuid4()
        self.store.create_collection(identity, CollectionSpec(**kwargs))
        return identity

    def admit(self, collection, url="https://example.com/"):
        return self.store.admit(collection, url, self.context, self.policy, now=self.now)

    def complete(self, acquisition_id, generation, *, success, outcome, now, navigation=None):
        from periplus.platform.catalogue.records import VisitEvidence, VisitRecord
        acquisition = self.store.get_acquisition(acquisition_id)
        if acquisition.status == "dispatched" and acquisition.generation == generation:
            self.store.begin_attempt(acquisition_id, generation, now=self.now)
            acquisition = self.store.get_acquisition(acquisition_id)
        if acquisition.outcome:
            attempts = tuple(AttemptRecord.model_validate(value) for value in acquisition.outcome["attempts"])
        else:
            attempts = (AttemptRecord(
                attempt_id=attempt_id_for(acquisition_id, 0), visit_id=acquisition_id, attempt_index=0,
                started_at=self.now, finished_at=now, outcome="succeeded",
                resource_usage=AttemptUsage(policy_version=acquisition.dispatch_policy_version,
                                            domain_policy=acquisition.attempt_domain_policy,
                                            exclusion_policy_version=acquisition.attempt_exclusion_version,
                                            reserved_ms=acquisition.attempt_reserved_ms or 125000, measured_ms=100),
            ),)
        evidence = VisitEvidence(visit=VisitRecord(
            visit_id=acquisition_id, requested_url=acquisition.url, visibility=acquisition.visibility,
            admitted_at=self.now, started_at=self.now, finished_at=now,
            outcome="succeeded" if success else "failed",
        ), attempts=attempts)
        return self.store.complete(acquisition_id, generation, evidence=evidence,
                                   navigation=navigation, now=now)

    def test_domain_deferral_releases_unstarted_allowance_without_recharging_pages(self):
        identity = self.collection(page_limit=3)
        first = self.admit(identity)
        second = self.admit(identity, "https://example.com/next")
        other = self.admit(identity, "https://other.example/")
        work = self.store.dispatch(first.acquisition_id, now=self.now)
        self.assertTrue(self.store.defer_unstarted(work.acquisition_id, work.generation,
                                                  delay_seconds=60, now=self.now,
                                                  domain_policy=self.store.current_domain_policy(work.acquisition_id)))
        self.assertFalse(self.store.defer_unstarted(work.acquisition_id, work.generation,
                                                   delay_seconds=60, now=self.now))
        with self.sessions() as session:
            control = session.get(FrontierControlRecord, 1)
            self.assertEqual((control.active_count, control.pending_count, control.reserved_attempts,
                              control.started_attempts, control.reserved_capture_ms, control.charged_capture_ms),
                             (0, 3, 0, 0, 0, 0))
        self.assertEqual(self.store.get_collection(identity).consumed, 1)
        self.assertEqual(self.store.get_acquisition(first.acquisition_id).attempt_count, 0)
        self.assertIsNone(self.store.dispatch(second.acquisition_id, now=self.now))
        other_work = self.store.dispatch_next(now=self.now)
        self.assertEqual(other_work.acquisition_id, other.acquisition_id)
        resumed = self.store.dispatch(first.acquisition_id, now=self.now + timedelta(seconds=61))
        self.assertIsNotNone(resumed)
        self.assertEqual(self.store.get_collection(identity).consumed, 2)
        self.assertFalse(self.store.begin_attempt(first.acquisition_id, work.generation, now=self.now))
        self.assertTrue(self.store.begin_attempt(first.acquisition_id, resumed.generation,
                                               now=self.now + timedelta(seconds=61)))
        self.assertFalse(self.store.defer_unstarted(first.acquisition_id, resumed.generation,
                                                    delay_seconds=60, now=self.now))

    def domain_policy(self, host, **settings):
        from periplus.crawl.control.domain_policies.schemas import DomainPolicyCreateRequest
        from periplus.crawl.control.domain_policies.service import create_domain_policy
        with self.sessions.begin() as session:
            return create_domain_policy(session, DomainPolicyCreateRequest(slug=host.replace("*", "wild").replace(".", "-"),
                host_match=host, **settings)).id

    def edit_domain(self, identity, **changes):
        from periplus.crawl.control.domain_policies.service import update_domain_policy
        with self.sessions.begin() as session:
            policy = session.get(DomainPolicy, identity)
            return update_domain_policy(session, policy=policy, expected_version=policy.version, **changes).version

    def test_current_domain_pause_filters_before_window_and_resumes_existing_work(self):
        policy_id = self.domain_policy("slow.example", paused=True)
        identity = self.collection(page_limit=100)
        queued = [self.admit(identity, f"https://slow.example/{index}") for index in range(80)]
        other = self.admit(identity, "https://other.example/")
        self.assertIsNone(self.store.dispatch(queued[0].acquisition_id, now=self.now))
        self.assertEqual(self.store.dispatch_next(now=self.now).acquisition_id, other.acquisition_id)
        self.edit_domain(policy_id, paused=False)
        self.assertIn(self.store.dispatch_next(now=self.now).acquisition_id, {item.acquisition_id for item in queued})

    def test_current_domain_concurrency_and_final_start_version(self):
        policy_id = self.domain_policy("example.com", maximum_concurrency=1)
        identity = self.collection()
        first = self.admit(identity)
        second = self.admit(identity, "https://example.com/next")
        work = self.store.dispatch(first.acquisition_id, now=self.now)
        authorized = self.store.current_domain_policy(work.acquisition_id)
        self.assertIsNone(self.store.dispatch(second.acquisition_id, now=self.now))
        self.edit_domain(policy_id, maximum_concurrency=2)
        self.assertIsNotNone(self.store.dispatch(second.acquisition_id, now=self.now))
        self.assertFalse(self.store.begin_attempt(work.acquisition_id, work.generation,
                                                domain_policy=authorized, now=self.now))
        current = self.store.current_domain_policy(work.acquisition_id)
        self.assertEqual(current.version, 2)
        self.assertTrue(self.store.begin_attempt(work.acquisition_id, work.generation,
                                               domain_policy=current, now=self.now))
        self.edit_domain(policy_id, paused=True)
        from periplus.crawl.runtime.frontier_evidence import acquisition_context
        frozen = acquisition_context(self.store.get_acquisition(work.acquisition_id)).policy.domain
        self.assertEqual(frozen, current)
        self.assertEqual(frozen.updated_by, "admin")
        self.assertFalse(frozen.paused)

    def test_domain_hint_expires_on_policy_edit_without_erasing_retry_backoff(self):
        policy_id = self.domain_policy("example.com", minimum_request_interval_seconds=3600)
        first = self.admit(self.collection())
        work = self.store.dispatch(first.acquisition_id, now=self.now)
        self.store.defer_unstarted(work.acquisition_id, work.generation, now=self.now, delay_seconds=3600,
                                   domain_policy=self.store.current_domain_policy(work.acquisition_id))
        self.assertIsNone(self.store.dispatch_next(now=self.now))
        self.edit_domain(policy_id, minimum_request_interval_seconds=0)
        resumed = self.store.dispatch_next(now=self.now)
        self.assertIsNotNone(resumed)
        self.store.defer_unstarted(resumed.acquisition_id, resumed.generation, now=self.now, delay_seconds=60)
        self.edit_domain(policy_id, maximum_concurrency=2)
        self.assertIsNone(self.store.dispatch_next(now=self.now))
        self.assertIsNotNone(self.store.dispatch_next(now=self.now + timedelta(seconds=60)))

    def test_scheduler_policy_matching_agrees_with_specificity_and_disabled_rules(self):
        wildcard = self.domain_policy("*.example.com", paused=True)
        exact = self.domain_policy("api.example.com", paused=False)
        identity = self.collection(page_limit=10)
        blocked = self.admit(identity, "https://shop.example.com/")
        allowed = self.admit(identity, "https://api.example.com/")
        self.assertEqual(self.store.dispatch_next(now=self.now).acquisition_id, allowed.acquisition_id)
        self.assertIsNone(self.store.dispatch_next(now=self.now))
        self.edit_domain(wildcard, enabled=False)
        self.assertEqual(self.store.dispatch_next(now=self.now).acquisition_id, blocked.acquisition_id)

    def test_shared_acquisition_preserves_independent_budgets(self):
        first, second = self.collection(), self.collection()
        a, b = self.admit(first), self.admit(second)
        self.assertEqual(a.acquisition_id, b.acquisition_id)
        self.assertNotEqual(a.interest_id, b.interest_id)
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        self.complete(work.acquisition_id, work.generation, success=True,
                            outcome={"result": "ok"}, now=self.now)
        for identity in (first, second):
            record = self.store.get_collection(identity)
            self.assertEqual((record.reserved, record.consumed), (0, 1))
        with self.sessions() as session:
            events = list(session.scalars(select(FrontierOutboxRecord)))
        self.assertEqual(sum(e.kind == "observation" for e in events), 1)
        self.assertEqual(sum(e.kind == "lineage" and e.payload["kind"] == "fulfillment" for e in events), 2)

    def test_first_context_wins_even_if_later_path_is_shallower(self):
        identity = self.collection(max_depth=4)
        original = SelectionContext(depth=4, parent_observation_id=uuid4(), rule_id="links")
        first = self.store.admit(identity, "https://example.com/#one", original,
                                  self.policy, now=self.now)
        second = self.admit(identity, "https://example.com/#two")
        self.assertFalse(second.created)
        self.assertEqual(first.interest_id, second.interest_id)
        self.assertEqual(self.store.get_interest(first.interest_id).context,
                         original.model_dump(mode="json"))
        self.assertEqual(self.store.get_collection(identity).reserved, 1)

    def test_page_limit_dedup_and_release(self):
        identity = self.collection(page_limit=1)
        a = self.admit(identity)
        with self.assertRaises(CollectionUnavailable):
            self.admit(identity, "https://example.com/two")
        self.store.stop_collection(identity, now=self.now)
        self.assertEqual(self.store.get_collection(identity).reserved, 0)
        self.assertEqual(self.store.get_interest(a.interest_id).budget_state, "released")
        self.assertFalse(self.admit(identity).created)

    def test_cancelling_one_shared_caller_keeps_other_runnable(self):
        first, second = self.collection(), self.collection()
        a, b = self.admit(first), self.admit(second)
        self.store.stop_collection(first, now=self.now)
        work = self.store.dispatch(b.acquisition_id, now=self.now)
        self.assertIsNotNone(work)
        self.assertEqual(len(self.store.get_acquisition(a.acquisition_id).frozen_reasons), 1)
        self.assertEqual(self.store.get_collection(first).consumed, 0)
        self.assertEqual(self.store.get_collection(second).consumed, 1)

    def test_cancellation_after_dispatch_keeps_charge_and_other_result(self):
        first, second = self.collection(), self.collection()
        a, b = self.admit(first), self.admit(second)
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        self.store.stop_collection(first, now=self.now)
        self.complete(work.acquisition_id, work.generation, success=True,
                            outcome={"result": "ok"}, now=self.now)
        self.assertEqual(self.store.get_collection(first).consumed, 1)
        self.assertEqual(self.store.get_interest(a.interest_id).status, "cancelled")
        self.assertEqual(self.store.get_interest(b.interest_id).status, "selecting")

    def test_no_inflight_join_but_recent_terminal_reuse(self):
        first = self.admit(self.collection())
        work = self.store.dispatch(first.acquisition_id, now=self.now)
        second = self.admit(self.collection())
        self.assertNotEqual(first.acquisition_id, second.acquisition_id)
        self.complete(work.acquisition_id, work.generation, success=True,
                            outcome={"result": "ok"}, now=self.now)
        reused = self.admit(self.collection())
        self.assertEqual(reused.mode, "reused")
        self.assertEqual(reused.acquisition_id, first.acquisition_id)
        fresh = self.admit(self.collection(result_max_age_seconds=0))
        self.assertNotEqual(fresh.acquisition_id, first.acquisition_id)

    def test_reuse_requires_navigation_and_obeys_age(self):
        first = self.admit(self.collection())
        work = self.store.dispatch(first.acquisition_id, now=self.now)
        self.complete(work.acquisition_id, work.generation, success=True,
                            outcome={"result": "ok"}, now=self.now)
        branch = self.admit(self.collection(max_depth=1))
        self.assertNotEqual(branch.acquisition_id, first.acquisition_id)
        late = self.store.admit(self.collection(), "https://example.com/", self.context,
                               self.policy, now=self.now + timedelta(minutes=6))
        self.assertNotEqual(late.acquisition_id, first.acquisition_id)

    def test_branch_reuses_retained_navigation(self):
        first = self.admit(self.collection())
        work = self.store.dispatch(first.acquisition_id, now=self.now)
        self.complete(work.acquisition_id, work.generation, success=True,
                            outcome={"result": "ok"}, navigation=navigation_package(), now=self.now)
        branch = self.admit(self.collection(max_depth=1))
        self.assertEqual(branch.acquisition_id, first.acquisition_id)

    def test_private_collections_and_capture_requirements_do_not_share(self):
        public = self.admit(self.collection())
        private_a = self.admit(self.collection(visibility="private"))
        private_b = self.admit(self.collection(visibility="private"))
        self.assertEqual(len({public.acquisition_id, private_a.acquisition_id,
                              private_b.acquisition_id}), 3)
        other = self.store.admit(self.collection(), "https://example.com/", self.context,
                                EffectivePolicySnapshot.model_validate(policy_snapshot()), now=self.now)
        self.assertNotEqual(public.acquisition_id, other.acquisition_id)

    def test_admission_independent_of_dispatch_capacity(self):
        with self.sessions.begin() as session:
            control = session.get(FrontierControlRecord, 1)
            control.admission_limit = 3
            control.dispatch_limit = 1
        first = self.admit(self.collection())
        self.store.dispatch(first.acquisition_id, now=self.now)
        for index in range(3):
            item = self.admit(self.collection(), f"https://example.com/{index}")
            self.assertIsNone(self.store.dispatch(item.acquisition_id, now=self.now))
        with self.assertRaises(AdmissionDeferred):
            self.admit(self.collection(), "https://example.com/overflow")
        self.assertEqual(self.admit(self.collection(), "https://example.com/0").mode, "shared")

    def test_terminal_replay_and_stale_dispatch(self):
        a = self.admit(self.collection())
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        self.assertIsNone(self.store.dispatch(a.acquisition_id, now=self.now))
        self.assertTrue(self.complete(a.acquisition_id, work.generation, success=False,
                                            outcome={"error": "failed"}, now=self.now))
        self.assertFalse(self.complete(a.acquisition_id, work.generation, success=False,
                                             outcome={"error": "failed"}, now=self.now))
        with self.assertRaises(ValueError):
            self.complete(a.acquisition_id, work.generation, success=True,
                                outcome={"result": "different"}, now=self.now)
        with self.assertRaises(StaleDispatch):
            self.complete(a.acquisition_id, work.generation + 1, success=False,
                                outcome={"error": "failed"}, now=self.now)
        self.assertNotEqual(self.admit(self.collection()).acquisition_id, a.acquisition_id)

    def test_recovery_fences_old_delivery_without_second_page_charge(self):
        collection = self.collection()
        a = self.admit(collection)
        work = self.store.dispatch(a.acquisition_id, now=self.now, lease_seconds=10)
        self.assertTrue(self.store.begin_attempt(a.acquisition_id, work.generation,
                                                now=self.now, lease_seconds=10))
        self.assertFalse(self.store.begin_attempt(a.acquisition_id, work.generation, now=self.now))
        recovered = self.store.recover_dispatch(a.acquisition_id,
                                                now=self.now + timedelta(seconds=11))
        self.assertIsNone(recovered)
        self.assertEqual(self.store.get_acquisition(a.acquisition_id).generation, work.generation + 1)
        self.assertEqual(self.store.get_acquisition(a.acquisition_id).status, "retry")
        with self.assertRaises(StaleDispatch):
            self.complete(a.acquisition_id, work.generation, success=True,
                                outcome={"stale": True}, now=self.now + timedelta(seconds=12))
        self.assertEqual(self.store.get_collection(collection).consumed, 1)
        self.assertEqual(len(self.store.get_acquisition(a.acquisition_id).uncertain_attempts), 1)

    def test_attempt_exhaustion_never_restarts_remote_work(self):
        a = self.admit(self.collection())
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        with self.sessions.begin() as session:
            session.get(AcquisitionRecord, a.acquisition_id).attempt_limit = 1
        self.assertTrue(self.store.begin_attempt(a.acquisition_id, work.generation,
                                                now=self.now, lease_seconds=1))
        self.assertIsNone(self.store.recover_dispatch(a.acquisition_id,
                                                      now=self.now + timedelta(seconds=2)))
        record = self.store.get_acquisition(a.acquisition_id)
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.outcome["attempts"][0]["outcome"], "uncertain")
        self.assertIsNone(record.outcome["attempts"][0]["finished_at"])
        self.assertFalse(self.store.begin_attempt(a.acquisition_id, record.generation,
                                                  now=self.now + timedelta(seconds=3)))
        self.assertIsNone(self.store.recover_dispatch(a.acquisition_id,
                                                      now=self.now + timedelta(seconds=4)))
        self.assertEqual(len(self.store.get_acquisition(a.acquisition_id).uncertain_attempts), 1)

    def test_paused_shared_caller_does_not_block_other_caller(self):
        first, second = self.collection(), self.collection()
        a, b = self.admit(first), self.admit(second)
        self.store.set_collection_paused(first, True)
        work = self.store.dispatch(b.acquisition_id, now=self.now)
        self.assertIsNotNone(work)
        paused = self.store.get_interest(a.interest_id)
        self.assertNotEqual(paused.acquisition_id, b.acquisition_id)
        self.assertEqual(paused.budget_state, "reserved")
        self.assertIsNone(self.store.dispatch(paused.acquisition_id, now=self.now))
        self.store.set_collection_paused(first, False)
        self.assertIsNotNone(self.store.dispatch(paused.acquisition_id, now=self.now))
        self.assertEqual(self.store.get_collection(first).consumed, 1)

    def test_outbox_reclaims_expired_claim_and_rejects_stale_publisher(self):
        a = self.admit(self.collection())
        self.store.dispatch(a.acquisition_id, now=self.now)
        first = self.store.claim_outbox(now=self.now, lease_seconds=2)
        self.assertEqual(len(first), 3)
        self.assertEqual(self.store.claim_outbox(now=self.now), [])
        later = self.now + timedelta(seconds=3)
        second = self.store.claim_outbox(now=later)
        self.assertEqual(first[0].message_id, second[0].message_id)
        self.assertNotEqual(first[0].claim_token, second[0].claim_token)
        self.assertFalse(self.store.mark_outbox_published(first[0], now=later))
        self.assertFalse(self.store.release_outbox(first[0], "late error", now=later))
        for delivery in second:
            self.assertTrue(self.store.mark_outbox_published(delivery, now=later))
        self.assertEqual(self.store.claim_outbox(now=later + timedelta(minutes=2)), [])

    def test_outbox_failed_publication_has_bounded_backoff(self):
        a = self.admit(self.collection())
        self.store.dispatch(a.acquisition_id, now=self.now)
        delivery = self.store.claim_outbox(now=self.now)[0]
        self.assertTrue(self.store.release_outbox(delivery, "unavailable", now=self.now))
        self.assertEqual(self.store.claim_outbox(now=self.now + timedelta(seconds=4)), [])
        retry = self.store.claim_outbox(now=self.now + timedelta(seconds=5))
        self.assertEqual([item.message_id for item in retry], [delivery.message_id])
        with self.sessions() as session:
            record = session.get(FrontierOutboxRecord, delivery.message_id)
            self.assertEqual(record.publish_attempts, 2)
            self.assertEqual(record.last_error, "unavailable")

    def test_collection_settlement_waits_for_own_selection_only(self):
        identity = self.collection()
        own = self.admit(identity)
        unrelated = self.admit(self.collection(), "https://elsewhere.example/")
        self.assertIsNone(self.store.settle_collection(identity, now=self.now))
        self.store.finish_seed_selection(identity)
        with self.assertRaises(ValueError):
            self.store.finish_link_selection(own.interest_id)
        work = self.store.dispatch(own.acquisition_id, now=self.now)
        self.complete(work.acquisition_id, work.generation, success=True,
                            outcome={"result": "ok"}, now=self.now)
        self.assertIsNone(self.store.settle_collection(identity, now=self.now))
        self.store.finish_link_selection(own.interest_id)
        self.assertEqual(self.store.settle_collection(identity, now=self.now), "eligible_links_exhausted")
        self.assertEqual(self.store.get_acquisition(unrelated.acquisition_id).status, "queued")
        self.store.finish_link_selection(own.interest_id)
        self.assertEqual(self.store.settle_collection(identity, now=self.now), "eligible_links_exhausted")

    def test_budget_reached_still_waits_for_admitted_results(self):
        identity = self.collection(page_limit=1)
        own = self.admit(identity)
        self.store.finish_seed_selection(identity)
        work = self.store.dispatch(own.acquisition_id, now=self.now)
        self.assertIsNone(self.store.settle_collection(identity, now=self.now))
        self.complete(work.acquisition_id, work.generation, success=False,
                            outcome={"error": "failed"}, now=self.now)
        self.assertEqual(self.store.settle_collection(identity, now=self.now), "budget_reached")

    def test_empty_seed_result_can_settle_without_an_acquisition(self):
        identity = self.collection()
        self.store.finish_seed_selection(identity)
        self.assertEqual(self.store.settle_collection(identity, now=self.now), "eligible_links_exhausted")

    def test_outcome_identity_and_navigation_are_part_of_frozen_acceptance(self):
        from periplus.platform.catalogue.records import VisitEvidence, VisitRecord
        a = self.admit(self.collection())
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        wrong = VisitEvidence(visit=VisitRecord(
            visit_id=uuid4(), requested_url="https://example.com/",
            admitted_at=self.now, finished_at=self.now, outcome="failed",
        ), attempts=())
        with self.assertRaisesRegex(ValueError, "different acquisition"):
            self.store.complete(a.acquisition_id, work.generation, evidence=wrong, now=self.now)
        self.complete(a.acquisition_id, work.generation, success=True, outcome={},
                      navigation=navigation_package(), now=self.now)
        with self.assertRaisesRegex(ValueError, "conflicting terminal outcome"):
            self.complete(a.acquisition_id, work.generation, success=True, outcome={}, now=self.now)

    def test_outcome_cannot_change_acquisition_visibility_or_url(self):
        from periplus.crawl.runtime.frontier_evidence import terminal_evidence
        a = self.admit(self.collection(visibility="private"))
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        evidence = terminal_evidence(self.store.get_acquisition(a.acquisition_id), self.now, "failed")
        self.assertEqual(evidence.visit.visibility, "private")
        for change in ({"visibility": "public"}, {"requested_url": "https://other.example/"}):
            wrong = evidence.model_copy(update={"visit": evidence.visit.model_copy(update=change)})
            with self.assertRaisesRegex(ValueError, "URL or visibility"):
                self.store.complete(a.acquisition_id, work.generation, evidence=wrong, now=self.now)
        self.assertTrue(self.complete(a.acquisition_id, work.generation, success=False, outcome={}, now=self.now))

    def test_scheduler_rotates_collections_instead_of_counting_queued_urls(self):
        big, small = self.collection(page_limit=20), self.collection()
        large = [self.admit(big, f"https://large.example/{i}") for i in range(10)]
        other = self.admit(small, "https://small.example/")
        first = self.store.dispatch_next(now=self.now)
        second = self.store.dispatch_next(now=self.now)
        self.assertIn(first.acquisition_id, {item.acquisition_id for item in large})
        self.assertEqual(second.acquisition_id, other.acquisition_id)

    def test_domain_saturation_does_not_hide_other_domains(self):
        identity = self.collection(page_limit=100)
        for index in range(70):
            self.admit(identity, f"https://slow.example/{index}")
        for _ in range(4):
            self.assertIsNotNone(self.store.dispatch_next(now=self.now))
        other = self.admit(self.collection(), "https://available.example/")
        work = self.store.dispatch_next(now=self.now)
        self.assertEqual(work.acquisition_id, other.acquisition_id)
        self.assertIsNone(self.store.dispatch_next(now=self.now))

    def test_domain_delay_is_applied_before_candidate_window(self):
        identity = self.collection(page_limit=100)
        for index in range(70):
            self.admit(identity, f"https://slow.example/{index}")
        dispatched = self.store.dispatch_next(now=self.now)
        self.store.defer_unstarted(dispatched.acquisition_id, dispatched.generation, now=self.now, delay_seconds=30,
                                   domain_policy=self.store.current_domain_policy(dispatched.acquisition_id))
        other = self.admit(self.collection(), "https://available.example/")
        work = self.store.dispatch_next(now=self.now)
        self.assertEqual(work.acquisition_id, other.acquisition_id)
        self.assertIsNone(self.store.dispatch_next(now=self.now))
        self.assertIsNotNone(self.store.dispatch_next(now=self.now + timedelta(seconds=30)))

    def test_priority_preference_is_bounded_and_does_not_starve_waiting_collection(self):
        high = uuid4()
        self.store.create_collection(high, CollectionSpec(page_limit=25), priority=10)
        for index in range(25):
            self.admit(high, f"https://priority.example/{index}")
        waiting = self.admit(self.collection(), "https://waiting.example/")
        selected = []
        for _ in range(12):
            work = self.store.dispatch_next(now=self.now)
            self.assertIsNotNone(work)
            selected.append(work.acquisition_id)
            if work.acquisition_id == waiting.acquisition_id:
                break
            self.complete(work.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        self.assertIn(waiting.acquisition_id, selected)

    def test_frozen_seed_sql_resumes_without_running_query_again(self):
        from unittest.mock import Mock
        from periplus.crawl.runtime.frontier_selection import process_seed_selection
        from periplus.crawl.runtime.selection_contract import SelectionCheckpoint
        identity = self.collection(seed_sql="SELECT requested_url AS url FROM web.observation WHERE outcome = ?",
                                   seed_parameters=("succeeded",), page_limit=3)
        query = Mock(return_value=SelectionCheckpoint(
            urls=tuple(f"https://example.com/{index}" for index in range(3)), source_snapshot="snapshot:17",
            source_query_id="query-17", selected_at=self.now))
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).admission_limit = 1
        policy = lambda url: self.policy
        self.assertEqual(process_seed_selection(self.store, identity, policy, seed_query=query), "waiting")
        checkpoint = self.store.get_collection(identity).selection_checkpoint
        self.assertEqual((checkpoint["cursor"], checkpoint["source_snapshot"]), (1, "snapshot:17"))
        self.assertEqual(self.store.get_collection(identity).waiting_reason, "frontier_admission_capacity")
        self.assertIsNotNone(self.store.dispatch_next())
        self.assertEqual(process_seed_selection(self.store, identity, policy, seed_query=query), "waiting")
        self.assertIsNotNone(self.store.dispatch_next())
        self.assertEqual(process_seed_selection(self.store, identity, policy, seed_query=query), "settled")
        from periplus.query.service import QueryRequest
        query.assert_called_once_with(QueryRequest(
            sql="SELECT requested_url AS url FROM web.observation WHERE outcome = ?", parameters=["succeeded"]))
        self.assertEqual(checkpoint["source_query_id"], "query-17")
        self.assertEqual(checkpoint["selected_at"], self.now.isoformat().replace("+00:00", "Z"))
        self.assertEqual(self.store.get_collection(identity).reserved, 1)
        self.assertTrue(self.store.get_collection(identity).seeds_settled)

    def test_selection_cursor_cannot_skip_unadmitted_url(self):
        from periplus.crawl.runtime.selection_contract import SelectionCheckpoint
        identity = self.collection()
        first = self.store.freeze_selection(identity, SelectionCheckpoint(urls=("https://example.com/",)), seeds=True)
        second = self.store.freeze_selection(identity, SelectionCheckpoint(urls=("https://other.example/",)), seeds=True)
        self.assertEqual(first, second)
        with self.assertRaisesRegex(ValueError, "unaccounted"):
            self.store.finish_seed_selection(identity)
        with self.assertRaisesRegex(ValueError, "unaccounted"):
            self.store.advance_selection(identity, 0, seeds=True)
        self.admit(identity)
        self.assertTrue(self.store.advance_selection(identity, 0, seeds=True))
        self.assertFalse(self.store.advance_selection(identity, 0, seeds=True))

    def test_shared_capture_runs_distinct_follow_rules(self):
        import hashlib
        import io
        from types import SimpleNamespace
        from test_selection_sql import navigation_bytes
        from periplus.crawl.runtime.frontier_selection import process_link_selection
        from periplus.crawl.runtime.navigation_contract import NavigationPackage
        first = self.collection(max_depth=1, follow_sql="SELECT target_url AS url FROM nav.links WHERE target_url LIKE '%/a'")
        second = self.collection(max_depth=1, follow_sql="SELECT target_url AS url FROM nav.links WHERE target_url LIKE '%/b'")
        a, b = self.admit(first), self.admit(second)
        payload = navigation_bytes(["https://example.com/a", "https://example.com/b"])
        package = NavigationPackage(object_name="runtime/navigation/test.arrow", sha256=hashlib.sha256(payload).hexdigest(),
                                    schema_version=1, recipe="test", row_count=2, byte_size=len(payload))
        objects = SimpleNamespace(size=lambda name: len(payload), open=lambda name: io.BytesIO(payload))
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        self.complete(work.acquisition_id, work.generation, success=True, outcome={}, navigation=package, now=self.now)
        for item in (a, b):
            self.assertEqual(process_link_selection(self.store, item.interest_id, lambda url: self.policy, objects), "settled")
        with self.sessions() as session:
            for identity, selected in ((first, "https://example.com/a"), (second, "https://example.com/b")):
                urls = set(session.scalars(select(InterestRecord.url).where(InterestRecord.collection_id == identity)))
                self.assertEqual(urls, {"https://example.com/", selected})

    def test_expired_seed_selection_settles_without_evaluating_sql(self):
        from unittest.mock import Mock
        from periplus.crawl.runtime.frontier_selection import process_seed_selection
        identity = self.collection(seed_sql="SELECT requested_url AS url FROM web.observation",
                                   deadline_at=datetime.now(UTC) - timedelta(seconds=1))
        query = Mock()
        self.assertEqual(process_seed_selection(self.store, identity, lambda url: self.policy, seed_query=query), "settled")
        query.assert_not_called()
        self.assertEqual(self.store.get_collection(identity).outcome, "deadline")

    def test_retry_releases_dispatch_capacity_without_another_page_charge(self):
        from periplus.crawl.acquisition.models import AcquisitionAttemptEvidence, AcquisitionResult
        identity = self.collection()
        a = self.admit(identity)
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        self.assertTrue(self.store.begin_attempt(a.acquisition_id, work.generation, now=self.now))
        failure = AcquisitionResult(url="https://example.com/", success=False, duration_seconds=1,
                                    outcome="failed", failure_retryable=True,
                                    attempt_evidence=AcquisitionAttemptEvidence(
                                        resource_usage=AttemptUsage(policy_version=1, domain_policy=self.store.current_domain_policy(a.acquisition_id), exclusion_policy_version=1, reserved_ms=125000, measured_ms=100),
                                        attempt=1, started_at=self.now, completed_at=self.now,
                                        requested_url="https://example.com/", outcome="retry",
                                    ))
        self.assertTrue(self.store.defer_retry(a.acquisition_id, work.generation, failure,
                                               now=self.now, delay_seconds=10))
        self.assertIsNone(self.store.dispatch_next(now=self.now))
        with self.sessions() as session:
            control = session.get(FrontierControlRecord, 1)
            self.assertEqual((control.pending_count, control.active_count), (1, 0))
        retried = self.store.dispatch_next(now=self.now + timedelta(seconds=10))
        self.assertEqual(retried.acquisition_id, a.acquisition_id)
        self.assertGreater(retried.generation, work.generation)
        self.assertEqual(self.store.get_collection(identity).consumed, 1)
        self.assertEqual(len(self.store.get_acquisition(a.acquisition_id).prior_results), 1)

    def test_stopping_last_retry_interest_reclaims_capacity_and_keeps_attempt_evidence(self):
        from periplus.crawl.acquisition.models import AcquisitionAttemptEvidence, AcquisitionResult
        identity = self.collection()
        a = self.admit(identity)
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        self.store.begin_attempt(a.acquisition_id, work.generation, now=self.now)
        result = AcquisitionResult(url="https://example.com/", success=False, duration_seconds=1,
                                   outcome="failed", attempt_evidence=AcquisitionAttemptEvidence(
                                        resource_usage=AttemptUsage(policy_version=1, domain_policy=self.store.current_domain_policy(a.acquisition_id), exclusion_policy_version=1, reserved_ms=125000, measured_ms=100),
                                       attempt=1, requested_url="https://example.com/", outcome="retry",
                                       started_at=self.now, completed_at=self.now,
                                   ))
        self.store.defer_retry(a.acquisition_id, work.generation, result, now=self.now)
        self.store.stop_collection(identity, now=self.now)
        acquisition = self.store.get_acquisition(a.acquisition_id)
        self.assertEqual(acquisition.status, "cancelled")
        self.assertEqual(len(acquisition.outcome["attempts"]), 1)
        with self.sessions() as session:
            self.assertEqual(session.get(FrontierControlRecord, 1).pending_count, 0)
            self.assertIsNotNone(session.get(FrontierOutboxRecord, f"observation:{a.acquisition_id}"))

    def test_delayed_delivery_rechecks_collection_deadline(self):
        identity = self.collection(deadline_at=self.now + timedelta(seconds=1))
        a = self.admit(identity)
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        self.assertFalse(self.store.begin_attempt(a.acquisition_id, work.generation,
                                                  now=self.now + timedelta(seconds=2)))
        self.assertEqual(self.store.get_acquisition(a.acquisition_id).attempt_count, 0)

    def test_collection_claim_expiry_backoff_and_competing_owner(self):
        identity = self.collection()
        now = datetime.now(UTC) + timedelta(seconds=1)
        work = self.store.claim_collection(now=now)
        self.assertEqual(work.collection_id, identity)
        self.assertIsNone(self.store.claim_collection(now=now))
        replacement = self.store.claim_collection(now=now + timedelta(seconds=121))
        self.assertNotEqual(work.token, replacement.token)
        self.assertFalse(self.store.release_collection(work, now=now + timedelta(seconds=122)))
        self.assertTrue(self.store.release_collection(replacement, error="dependency_unavailable",
                                                      now=now + timedelta(seconds=122)))
        self.assertIsNone(self.store.claim_collection(now=now + timedelta(seconds=123)))
        self.assertIsNotNone(self.store.claim_collection(now=now + timedelta(seconds=124)))

    def test_expired_paused_dispatch_releases_active_capacity(self):
        identity = self.collection()
        admitted = self.admit(identity)
        self.store.dispatch(admitted.acquisition_id, now=self.now, lease_seconds=1)
        self.store.set_collection_paused(identity, True)
        later = self.now + timedelta(seconds=2)
        self.assertEqual(self.store.expired_dispatches(now=later), (admitted.acquisition_id,))
        self.store.recover_dispatch(admitted.acquisition_id, now=later)
        with self.sessions() as session:
            control = session.get(FrontierControlRecord, 1)
            self.assertEqual((control.pending_count, control.active_count), (1, 0))
        self.assertIsNone(self.store.dispatch_next(now=later))
        self.store.set_collection_paused(identity, False)
        self.assertIsNotNone(self.store.dispatch_next(now=later))
        self.assertEqual(self.store.get_collection(identity).consumed, 1)

    def test_recovery_of_abandoned_started_capture_retains_uncertainty(self):
        identity = self.collection()
        admitted = self.admit(identity)
        work = self.store.dispatch(admitted.acquisition_id, now=self.now)
        self.store.begin_attempt(admitted.acquisition_id, work.generation, now=self.now, lease_seconds=1)
        self.store.stop_collection(identity, now=self.now)
        self.store.recover_dispatch(admitted.acquisition_id, now=self.now + timedelta(seconds=2))
        acquisition = self.store.get_acquisition(admitted.acquisition_id)
        self.assertEqual(acquisition.status, "cancelled")
        self.assertEqual(acquisition.outcome["attempts"][0]["outcome"], "uncertain")
        with self.sessions() as session:
            control = session.get(FrontierControlRecord, 1)
            self.assertEqual((control.pending_count, control.active_count), (0, 0))

    def test_retained_interests_bound_shared_and_reused_admission_without_losing_dedup(self):
        identity = self.collection(page_limit=3)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).interest_limit = 1
        first = self.admit(identity)
        self.assertFalse(self.admit(identity).created)
        with self.assertRaises(AdmissionDeferred):
            self.admit(self.collection())
        self.store.stop_collection(identity, now=self.now)
        # Cancelled interests stay deduplicated until acknowledged lineage cleanup.
        self.assertEqual(self.admit(identity).interest_id, first.interest_id)
        with self.sessions() as session:
            self.assertEqual(session.get(FrontierControlRecord, 1).interest_count, 1)

    def test_discovery_checkpoint_rejects_stale_claim_and_revision(self):
        from periplus.crawl.control.collections.discovery import DiscoveryState
        identity = self.collection(seed_description="Finnish robotics")
        now = datetime.now(UTC) + timedelta(seconds=1)
        work = self.store.claim_collection(now=now)
        first = DiscoveryState(revision=1, model="model-1", queries=("Finnish robotics",))
        self.assertTrue(self.store.checkpoint_discovery(work, 0, first, now=now))
        self.assertFalse(self.store.checkpoint_discovery(work, 0, first, now=now))
        replacement = self.store.claim_collection(now=now + timedelta(seconds=121))
        second = first.model_copy(update={"revision": 2, "searches": ((),)})
        self.assertFalse(self.store.checkpoint_discovery(work, 1, second, now=now + timedelta(seconds=122)))
        self.assertTrue(self.store.checkpoint_discovery(replacement, 1, second, now=now + timedelta(seconds=122)))
        self.store.stop_collection(identity)
        self.assertFalse(self.store.checkpoint_discovery(replacement, 2,
            second.model_copy(update={"revision": 3, "selected": ()}), now=now + timedelta(seconds=123)))
        self.assertEqual(self.store.get_collection(identity).discovery_state["revision"], 2)

    def test_description_seeds_wait_for_frozen_discovery_then_use_normal_admission(self):
        from periplus.crawl.control.collections.discovery import DiscoveryState
        from periplus.crawl.runtime.frontier_selection import process_seed_selection
        identity = self.collection(seed_description="Robots")
        self.assertEqual(process_seed_selection(self.store, identity, lambda url: self.policy), "waiting")
        self.assertEqual(self.store.get_collection(identity).reserved, 0)
        work = self.store.claim_collection()
        state = DiscoveryState(revision=1, model="model-1", queries=("Robots",), searches=((),),
                               selected=("https://example.com/",), validation_cursor=1, urls=("https://example.com/",))
        self.assertTrue(self.store.checkpoint_discovery(work, 0, state))
        self.assertEqual(process_seed_selection(self.store, identity, lambda url: self.policy), "settled")
        self.assertEqual(self.store.get_collection(identity).reserved, 1)
        self.assertTrue(self.store.get_collection(identity).seeds_settled)

    def test_control_changes_apply_to_future_dispatch_without_releasing_work(self):
        from periplus.crawl.control.collections.frontier_controls import ReplaceFrontierSettings
        first = self.admit(self.collection())
        initial = self.store.control_view()
        paused = self.store.replace_controls(ReplaceFrontierSettings(
            expected_version=initial.policy_version,
            settings=initial.settings.model_copy(update={"paused": True, "admission_limit": 1}),
        ), actor="test", now=self.now)
        self.assertIsNone(self.store.dispatch(first.acquisition_id, now=self.now))
        self.assertEqual(self.store.get_interest(first.interest_id).budget_state, "reserved")
        resumed = self.store.replace_controls(ReplaceFrontierSettings(
            expected_version=paused.policy_version,
            settings=paused.settings.model_copy(update={"paused": False}),
        ), actor="test", now=self.now)
        work = self.store.dispatch(first.acquisition_id, now=self.now)
        self.assertIsNotNone(work)
        self.assertEqual(self.store.get_acquisition(first.acquisition_id).dispatch_policy_version,
                         resumed.policy_version)
        slow = self.store.replace_controls(ReplaceFrontierSettings(
            expected_version=resumed.policy_version,
            settings=resumed.settings.model_copy(update={"captures_per_minute": 1}),
        ), actor="test", now=self.now + timedelta(seconds=1))
        self.assertEqual(slow.next_rate_eligibility_at, self.now + timedelta(seconds=60))
        second = self.admit(self.collection(), "https://other.example/")
        self.assertIsNone(self.store.dispatch(second.acquisition_id, now=self.now + timedelta(seconds=2)))
        self.assertIsNotNone(self.store.dispatch(second.acquisition_id, now=self.now + timedelta(seconds=60)))

    def test_physical_allowance_is_once_per_shared_dispatch_and_reconciles_once(self):
        from periplus.crawl.control.collections.frontier_controls import ReplaceFrontierSettings
        first, second = self.collection(), self.collection()
        a, b = self.admit(first), self.admit(second)
        initial = self.store.control_view()
        self.store.replace_controls(ReplaceFrontierSettings(
            expected_version=initial.policy_version,
            settings=initial.settings.model_copy(update={"attempt_allowance": 1}),
        ), actor="test", now=self.now)
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        reserved = self.store.control_view()
        self.assertEqual(a.acquisition_id, b.acquisition_id)
        self.assertEqual((reserved.reserved_attempts, reserved.started_attempts), (1, 0))
        self.assertEqual(reserved.reserved_capture_ms, 125000)
        self.assertEqual(reserved.dispatch_waiting_reason, "attempt_allowance_exhausted")
        other = self.admit(self.collection(), "https://other.example/")
        self.assertIsNone(self.store.dispatch(other.acquisition_id, now=self.now + timedelta(seconds=1)))
        self.assertEqual(self.store.get_interest(other.interest_id).budget_state, "reserved")
        self.complete(work.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        settled = self.store.control_view()
        self.assertEqual((settled.reserved_attempts, settled.started_attempts), (0, 1))
        self.assertEqual((settled.reserved_capture_ms, settled.charged_capture_ms), (0, 100))
        self.assertFalse(self.complete(work.acquisition_id, work.generation, success=True, outcome={}, now=self.now))
        self.assertEqual(self.store.control_view().charged_capture_ms, 100)
        self.assertEqual(self.store.get_collection(first).consumed, 1)
        self.assertEqual(self.store.get_collection(second).consumed, 1)

    def test_unstarted_dispatch_releases_physical_allowance_but_uncertain_start_is_charged(self):
        identity = self.collection()
        a = self.admit(identity)
        work = self.store.dispatch(a.acquisition_id, now=self.now, lease_seconds=1)
        self.store.recover_dispatch(a.acquisition_id, now=self.now + timedelta(seconds=2))
        released = self.store.control_view()
        self.assertEqual((released.reserved_attempts, released.started_attempts), (0, 0))
        self.assertEqual((released.reserved_capture_ms, released.charged_capture_ms), (0, 0))
        retry = self.store.dispatch(a.acquisition_id, now=self.now + timedelta(seconds=2), lease_seconds=1)
        self.store.begin_attempt(a.acquisition_id, retry.generation, now=self.now + timedelta(seconds=2), lease_seconds=1)
        self.store.recover_dispatch(a.acquisition_id, now=self.now + timedelta(seconds=4))
        charged = self.store.control_view()
        self.assertEqual((charged.reserved_attempts, charged.started_attempts), (0, 1))
        self.assertEqual((charged.reserved_capture_ms, charged.charged_capture_ms), (0, 125000))
        self.store.recover_dispatch(a.acquisition_id, now=self.now + timedelta(seconds=5))
        self.assertEqual(self.store.control_view().charged_capture_ms, 125000)
        self.store.stop_collection(identity, now=self.now + timedelta(seconds=5))
        evidence = self.store.get_acquisition(a.acquisition_id).outcome
        self.assertEqual(evidence["attempts"][0]["resource_usage"]["reserved_ms"], 125000)
        self.assertIsNone(evidence["attempts"][0]["resource_usage"]["measured_ms"])

    def test_time_allowance_blocks_dispatch_and_operator_can_increase_total(self):
        from periplus.crawl.control.collections.frontier_controls import ReplaceFrontierSettings
        initial = self.store.control_view()
        limited = self.store.replace_controls(ReplaceFrontierSettings(
            expected_version=initial.policy_version,
            settings=initial.settings.model_copy(update={"capture_time_allowance_ms": 124999}),
        ), actor="test", now=self.now)
        a = self.admit(self.collection())
        self.assertIsNone(self.store.dispatch(a.acquisition_id, now=self.now))
        self.assertEqual(limited.dispatch_waiting_reason, "capture_time_allowance_exhausted")
        self.store.replace_controls(ReplaceFrontierSettings(
            expected_version=limited.policy_version,
            settings=limited.settings.model_copy(update={"capture_time_allowance_ms": 125000}),
        ), actor="test", now=self.now)
        self.assertIsNotNone(self.store.dispatch(a.acquisition_id, now=self.now))
        self.assertEqual(self.store.control_view().reserved_capture_ms, 125000)

    def test_known_time_overrun_is_not_clipped_and_mismatched_reservation_is_rejected(self):
        from periplus.platform.catalogue.records import VisitEvidence, VisitRecord
        a = self.admit(self.collection())
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        self.store.begin_attempt(a.acquisition_id, work.generation, now=self.now)
        attempt = AttemptRecord(
            attempt_id=attempt_id_for(a.acquisition_id, 0), visit_id=a.acquisition_id, attempt_index=0,
            started_at=self.now, finished_at=self.now, outcome="succeeded",
            resource_usage=AttemptUsage(policy_version=work.generation, domain_policy=self.store.current_domain_policy(a.acquisition_id), exclusion_policy_version=1, reserved_ms=125000, measured_ms=130000),
        )
        evidence = VisitEvidence(visit=VisitRecord(
            visit_id=a.acquisition_id, requested_url="https://example.com/", admitted_at=self.now,
            started_at=self.now, finished_at=self.now, outcome="succeeded",
        ), attempts=(attempt,))
        wrong = evidence.model_copy(update={"attempts": (attempt.model_copy(update={
            "resource_usage": attempt.resource_usage.model_copy(update={"reserved_ms": 1}),
        }),)})
        with self.assertRaisesRegex(ValueError, "frozen reservation"):
            self.store.complete(a.acquisition_id, work.generation, evidence=wrong, now=self.now)
        self.assertEqual(self.store.control_view().reserved_capture_ms, 125000)
        self.store.complete(a.acquisition_id, work.generation, evidence=evidence, now=self.now)
        self.assertEqual(self.store.control_view().charged_capture_ms, 130000)

    def test_retry_usage_remains_in_terminal_evidence_without_second_page_charge(self):
        from periplus.crawl.acquisition.models import AcquisitionAttemptEvidence, AcquisitionResult
        from periplus.crawl.acquisition.evidence import attempt_records
        from periplus.crawl.runtime.frontier_evidence import acquisition_context
        from periplus.platform.catalogue.records import VisitEvidence, VisitRecord
        identity = self.collection()
        a = self.admit(identity)
        first = self.store.dispatch(a.acquisition_id, now=self.now)
        self.store.begin_attempt(a.acquisition_id, first.generation, now=self.now)
        failed = AcquisitionResult(url="https://example.com/", success=False, duration_seconds=0.1,
            outcome="failed", attempt_evidence=AcquisitionAttemptEvidence(
                attempt=1, requested_url="https://example.com/", outcome="retry",
                started_at=self.now, completed_at=self.now,
                resource_usage=AttemptUsage(policy_version=1, domain_policy=self.store.current_domain_policy(a.acquisition_id), exclusion_policy_version=1, reserved_ms=125000, measured_ms=100),
            ))
        self.store.defer_retry(a.acquisition_id, first.generation, failed, now=self.now, delay_seconds=0)
        later = self.now + timedelta(seconds=1)
        second = self.store.dispatch(a.acquisition_id, now=later)
        self.store.begin_attempt(a.acquisition_id, second.generation, now=later)
        context = acquisition_context(self.store.get_acquisition(a.acquisition_id))
        final = AcquisitionAttemptEvidence(
            attempt=2, requested_url="https://example.com/", outcome="success",
            started_at=later, completed_at=later,
            resource_usage=AttemptUsage(policy_version=1, domain_policy=self.store.current_domain_policy(a.acquisition_id), exclusion_policy_version=1, reserved_ms=125000, measured_ms=200),
        )
        attempts = attempt_records(a.acquisition_id, context.prior_attempts + (final,))
        evidence = VisitEvidence(visit=VisitRecord(
            visit_id=a.acquisition_id, requested_url="https://example.com/",
            admitted_at=self.now, started_at=self.now, finished_at=later, outcome="succeeded",
        ), attempts=attempts)
        changed = evidence.model_copy(update={"attempts": (attempts[0].model_copy(update={
            "resource_usage": attempts[0].resource_usage.model_copy(update={"measured_ms": 0}),
        }), attempts[1])})
        with self.assertRaisesRegex(ValueError, "previously recorded"):
            self.store.complete(a.acquisition_id, second.generation, evidence=changed, now=later)
        self.store.complete(a.acquisition_id, second.generation, evidence=evidence, now=later)
        self.assertEqual(self.store.control_view().charged_capture_ms, 300)
        self.assertEqual(self.store.control_view().started_attempts, 2)
        self.assertEqual(self.store.control_view().reserved_capture_ms, 0)
        self.assertEqual(self.store.get_collection(identity).consumed, 1)
        self.assertEqual(self.store.get_acquisition(a.acquisition_id).outcome["attempts"][0]["resource_usage"]["measured_ms"], 100)

    def test_ingestion_receipts_prove_evidence_and_lineage_separately_from_query_readiness(self):
        from periplus.ingestion.queue import IngestionState
        from periplus.platform.catalogue.records import IngestionWriteResult
        from periplus.crawl.runtime.frontier_views import collection_views
        identity = self.collection(max_depth=0)
        a = self.admit(identity)
        self.store.finish_seed_selection(identity)
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        self.complete(a.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        self.store.finish_link_selection(a.interest_id)
        self.store.settle_collection(identity, now=self.now)
        for delivery in self.store.claim_outbox(now=self.now):
            self.store.mark_outbox_published(delivery, now=self.now)
        self.assertIsNone(self.store.get_acquisition(a.acquisition_id).evidence_snapshot)
        before, = collection_views(self.sessions, identity=identity)
        self.assertEqual(before.ingested_pages, 0)
        self.assertFalse(before.lineage_ready)
        receipts = self.store.claim_ingestion_receipts(now=self.now)
        self.assertTrue(receipts)
        self.assertTrue(all(delivery.kind != "capture" for delivery in receipts))
        for delivery in receipts:
            job = delivery.ingestion_job()
            state = IngestionState(job=job, status="succeeded", updated_at=self.now,
                result=IngestionWriteResult(kind=job.kind, identity=job.identity, created=True, repository_snapshot=42))
            self.assertTrue(self.store.record_ingestion_receipt(delivery, state, now=self.now))
            self.assertFalse(self.store.record_ingestion_receipt(delivery, state, now=self.now))
        self.assertEqual(self.store.get_acquisition(a.acquisition_id).evidence_snapshot, 42)
        self.assertEqual(self.store.claim_ingestion_receipts(now=self.now + timedelta(days=1)), [])
        after, = collection_views(self.sessions, identity=identity)
        self.assertEqual(after.ingested_pages, 1)
        self.assertTrue(after.lineage_ready)
        self.assertIsNone(after.query_ready)
        self.assertEqual(after.query_readiness_reason, "materialization_commit_not_verified")

    def test_ingestion_receipts_reject_wrong_evidence_and_expired_claims(self):
        from periplus.ingestion.queue import IngestionState
        from periplus.platform.catalogue.records import IngestionWriteResult
        self.collection()
        publication, = self.store.claim_outbox(now=self.now)
        self.store.mark_outbox_published(publication, now=self.now)
        old, = self.store.claim_ingestion_receipts(now=self.now)
        self.assertEqual(self.store.claim_ingestion_receipts(now=self.now), [])
        later = self.now + timedelta(seconds=61)
        current, = self.store.claim_ingestion_receipts(now=later)
        job = old.ingestion_job()
        state = IngestionState(job=job, status="succeeded", updated_at=later,
            result=IngestionWriteResult(kind=job.kind, identity=job.identity, created=False, repository_snapshot=7))
        self.assertFalse(self.store.record_ingestion_receipt(old, state, now=later))
        wrong_job = job.model_copy(update={"lineage": job.lineage.model_copy(update={"specification": {}})})
        with self.assertRaisesRegex(ValueError, "different immutable evidence"):
            self.store.record_ingestion_receipt(current, state.model_copy(update={"job": wrong_job}), now=later)
        pending = IngestionState(job=job, status="pending", updated_at=later)
        with self.assertRaisesRegex(ValueError, "successful durable result"):
            self.store.record_ingestion_receipt(current, pending, now=later)
        self.assertTrue(self.store.defer_ingestion_receipt(current, "ingestion_pending", now=later))
        self.assertEqual(self.store.claim_ingestion_receipts(now=later + timedelta(seconds=19)), [])
        retried, = self.store.claim_ingestion_receipts(now=later + timedelta(seconds=20))
        self.assertTrue(self.store.record_ingestion_receipt(retried, state, now=later + timedelta(seconds=20)))

    def test_historical_check_snapshot_and_expiry_protect_operational_markers(self):
        from periplus.crawl.runtime.background_seen import HistoricalSeenResult, SeenCandidates
        own = self.admit(self.collection())
        work = self.store.dispatch(own.acquisition_id, now=self.now)
        self.complete(own.acquisition_id, work.generation, success=True, outcome={},
                      navigation=navigation_package(), now=self.now)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).background_share = 10
        candidates = SeenCandidates(urls=("https://example.com/next",))
        check = self.store.start_background_check(own.acquisition_id, candidates, now=self.now)
        def cleanup_allowed(snapshot, now):
            with self.sessions.begin() as session:
                self.store._control(session)
                return self.store._historical_checks_allow_cleanup(session, snapshot, now)
        self.assertFalse(cleanup_allowed(7, self.now))
        result = HistoricalSeenResult(candidates=candidates, seen_urls=(), snapshot=7, query_id="q")
        completed = self.store.finish_background_check(check, result, now=self.now)
        self.assertEqual(completed.result, result)
        self.assertTrue(cleanup_allowed(7, self.now))
        self.assertFalse(cleanup_allowed(8, self.now))
        self.assertTrue(cleanup_allowed(8, self.now + timedelta(seconds=121)))
        with self.assertRaises(StaleDispatch):
            self.store.finish_background_check(check, result, now=self.now + timedelta(seconds=121))
        replacement = self.store.start_background_check(own.acquisition_id, candidates,
                                                         now=self.now + timedelta(seconds=121))
        self.assertNotEqual(check.token, replacement.token)
        with self.assertRaises(StaleDispatch):
            self.store.finish_background_check(check, result, now=self.now + timedelta(seconds=121))
        self.assertFalse(cleanup_allowed(7, self.now + timedelta(seconds=121)))

    def test_private_navigation_and_disabled_background_cannot_start_historical_checks(self):
        from periplus.crawl.runtime.background_seen import SeenCandidates
        own = self.admit(self.collection(visibility="private"))
        work = self.store.dispatch(own.acquisition_id, now=self.now)
        self.complete(own.acquisition_id, work.generation, success=True, outcome={},
                      navigation=navigation_package(), now=self.now)
        candidates = SeenCandidates(urls=("https://example.com/next",))
        with self.assertRaises(AdmissionDeferred):
            self.store.start_background_check(own.acquisition_id, candidates, now=self.now)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).background_share = 10
        with self.assertRaisesRegex(ValueError, "public navigation"):
            self.store.start_background_check(own.acquisition_id, candidates, now=self.now)

    def checked_background(self, urls, seen=()):
        from periplus.crawl.runtime.background_seen import HistoricalSeenResult, SeenCandidates
        identity = self.collection(max_depth=0)
        parent = self.admit(identity, f"https://parent.example/{uuid4()}")
        work = self.store.dispatch(parent.acquisition_id, now=self.now)
        self.complete(parent.acquisition_id, work.generation, success=True, outcome={},
                      navigation=navigation_package(), now=self.now)
        self.store.finish_seed_selection(identity)
        self.store.finish_link_selection(parent.interest_id)
        self.store.settle_collection(identity, now=self.now)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).background_share = 10
        candidates = SeenCandidates(urls=tuple(urls))
        check = self.store.start_background_check(parent.acquisition_id, candidates, now=self.now)
        result = HistoricalSeenResult(candidates=candidates, seen_urls=tuple(seen), snapshot=7, query_id="seen-query")
        return identity, parent, self.store.finish_background_check(check, result, now=self.now)

    def test_background_admission_is_independent_shared_and_retains_lookup_provenance(self):
        identity, parent, check = self.checked_background(["https://child.example/"])
        before = self.store.get_collection(identity).consumed
        admission = self.store.admit_background(check, "https://child.example/", self.policy, now=self.now)
        self.assertEqual(admission.status, "admitted")
        self.assertEqual(self.store.admit_background(check, admission.url, self.policy, now=self.now), admission)
        self.assertEqual(self.store.get_collection(identity).status, "settled")
        self.assertEqual(self.store.get_collection(identity).consumed, before)
        self.assertTrue(self.store.get_acquisition(parent.acquisition_id).background_selected)
        with self.sessions() as session:
            self.assertEqual(len(list(session.scalars(select(CollectionRecord)))), 1)
            self.assertEqual(len(list(session.scalars(select(InterestRecord)))), 1)
        joined = self.collection()
        interest = self.admit(joined, admission.url)
        self.assertEqual(interest.acquisition_id, admission.acquisition_id)
        self.store.stop_collection(joined, now=self.now)
        self.assertEqual(self.store.get_acquisition(admission.acquisition_id).status, "queued")
        work = self.store.dispatch(admission.acquisition_id, now=self.now + timedelta(seconds=1))
        self.assertIsNotNone(work)
        with self.sessions() as session:
            reasons = [row.payload for row in session.scalars(select(FrontierOutboxRecord).where(
                FrontierOutboxRecord.acquisition_id == admission.acquisition_id,
                FrontierOutboxRecord.kind == "lineage"))]
        background, = [reason for reason in reasons if reason["kind"] == "acquisition_reason"]
        self.assertIsNone(background["collection_id"])
        self.assertEqual(background["parent_observation_id"], str(parent.acquisition_id))
        self.assertEqual(background["selection_provenance"], {
            "policy_version": 1, "source_snapshot": 7, "source_query_id": "seen-query",
        })

    def test_background_admission_closes_the_post_lookup_capture_gap(self):
        _, _, check = self.checked_background(["https://later.example/", "https://seen.example/"],
                                               seen=["https://seen.example/"])
        current = self.admit(self.collection(), "https://later.example/")
        work = self.store.dispatch(current.acquisition_id, now=self.now + timedelta(seconds=1))
        self.complete(current.acquisition_id, work.generation, success=True, outcome={}, now=self.now + timedelta(seconds=1))
        self.assertIsNone(self.store.get_acquisition(current.acquisition_id).evidence_snapshot)
        decision = self.store.admit_background(check, "https://later.example/", self.policy, now=self.now + timedelta(seconds=1))
        self.assertEqual((decision.status, decision.reason), ("seen", "operational_observation"))
        # Supplying an altered result cannot erase the stored historical decision.
        altered = check.model_copy(update={"result": check.result.model_copy(update={"seen_urls": ()})})
        self.assertEqual(self.store.admit_background(altered, "https://seen.example/", self.policy, now=self.now).status, "seen")

    def test_background_admission_rejects_expiry_policy_changes_and_unknown_candidates(self):
        _, _, check = self.checked_background(["https://child.example/"])
        with self.assertRaisesRegex(ValueError, "checked candidate"):
            self.store.admit_background(check, "https://other.example/", self.policy, now=self.now)
        with self.assertRaises(StaleDispatch):
            self.store.admit_background(check, "https://child.example/", self.policy, now=self.now + timedelta(seconds=121))
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).policy_version += 1
        with self.assertRaises(StaleDispatch):
            self.store.admit_background(check, "https://child.example/", self.policy, now=self.now)

    def test_zero_background_allocation_retains_queued_background_work(self):
        _, _, check = self.checked_background(["https://child.example/"])
        child = self.store.admit_background(check, "https://child.example/", self.policy, now=self.now)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).background_share = 0
        self.assertIsNone(self.store.dispatch(child.acquisition_id, now=self.now + timedelta(seconds=1)))
        self.assertEqual(self.store.get_acquisition(child.acquisition_id).status, "queued")
        with self.assertRaises(AdmissionDeferred):
            self.store.admit_background(check, child.url, self.policy, now=self.now)

    def test_background_capacity_preserves_undecided_candidates_and_traps_create_no_work(self):
        _, parent, check = self.checked_background([
            "https://example.com/calendar/2026", "https://first.example/", "https://second.example/",
        ])
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).admission_limit = 1
        declined = self.store.admit_background(check, "https://example.com/calendar/2026", self.policy, now=self.now)
        self.assertEqual((declined.status, declined.reason), ("declined", "calendar_or_session"))
        first = self.store.admit_background(check, "https://first.example/", self.policy, now=self.now)
        with self.assertRaises(AdmissionDeferred):
            self.store.admit_background(check, "https://second.example/", self.policy, now=self.now)
        self.assertFalse(self.store.get_acquisition(parent.acquisition_id).background_selected)
        self.store.dispatch(first.acquisition_id, now=self.now + timedelta(seconds=1))
        second = self.store.admit_background(check, "https://second.example/", self.policy, now=self.now + timedelta(seconds=1))
        self.assertEqual(second.status, "admitted")
        self.assertTrue(self.store.get_acquisition(parent.acquisition_id).background_selected)

    def test_scheduler_background_share_and_spare_capacity(self):
        urls = [f"https://background{i}.example/" for i in range(8)]
        _, _, check = self.checked_background(urls)
        for url in urls:
            self.store.admit_background(check, url, self.policy, now=self.now)
        request = self.collection(page_limit=40)
        for i in range(20):
            self.admit(request, f"https://request{i}.example/")
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).background_share = 25
        kinds = []
        for _ in range(20):
            work = self.store.dispatch_next(now=self.now)
            self.assertIsNotNone(work)
            acquisition = self.store.get_acquisition(work.acquisition_id)
            kinds.append(acquisition.attempt_background)
            self.complete(work.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        self.assertEqual(kinds, [False, False, False, True] * 5)
        self.store.stop_collection(request, now=self.now)
        for _ in range(3):
            work = self.store.dispatch_next(now=self.now)
            self.assertIsNotNone(work)
            self.assertTrue(self.store.get_acquisition(work.acquisition_id).attempt_background)
            self.complete(work.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        self.assertIsNone(self.store.dispatch_next(now=self.now))
        self.assertEqual(self.store.control_view().background_started_attempts, 8)
        self.assertEqual(self.store.control_view().background_charged_capture_ms, 800)

    def test_background_allowance_does_not_block_request_work_or_double_charge_sharing(self):
        urls = ["https://background.example/", "https://shared.example/"]
        _, _, check = self.checked_background(urls)
        first, shared = [self.store.admit_background(check, url, self.policy, now=self.now) for url in urls]
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).background_attempt_allowance = 0
        self.assertIsNone(self.store.dispatch_next(now=self.now))
        request = self.collection()
        own = self.admit(request, shared.url)
        self.assertEqual(own.acquisition_id, shared.acquisition_id)
        work = self.store.dispatch_next(now=self.now)
        self.assertEqual(work.acquisition_id, shared.acquisition_id)
        self.complete(work.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        self.assertEqual(self.store.control_view().background_started_attempts, 0)
        self.assertEqual(self.store.get_acquisition(first.acquisition_id).status, "queued")

    def test_last_request_cancellation_requires_background_allowance_before_start(self):
        _, _, check = self.checked_background(["https://shared.example/"])
        child = self.store.admit_background(check, "https://shared.example/", self.policy, now=self.now)
        request = self.collection()
        self.admit(request, child.url)
        work = self.store.dispatch_next(now=self.now)
        self.store.stop_collection(request, now=self.now)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).background_attempt_allowance = 0
        self.assertFalse(self.store.begin_attempt(work.acquisition_id, work.generation, now=self.now))
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).background_attempt_allowance = 1
        self.assertTrue(self.store.begin_attempt(work.acquisition_id, work.generation, now=self.now))
        view = self.store.control_view()
        self.assertEqual((view.background_reserved_attempts, view.background_started_attempts), (0, 1))
        self.assertEqual(view.background_reserved_capture_ms, 125000)
        self.assertFalse(self.store.begin_attempt(work.acquisition_id, work.generation, now=self.now))

    def test_background_recovery_releases_unstarted_and_charges_unknown_time(self):
        _, _, check = self.checked_background(["https://background.example/"])
        child = self.store.admit_background(check, "https://background.example/", self.policy, now=self.now)
        self.store.dispatch_next(now=self.now, lease_seconds=1)
        self.store.recover_dispatch(child.acquisition_id, now=self.now + timedelta(seconds=2))
        view = self.store.control_view()
        self.assertEqual((view.background_reserved_attempts, view.background_started_attempts), (0, 0))
        self.assertEqual((view.background_reserved_capture_ms, view.background_charged_capture_ms), (0, 0))
        work = self.store.dispatch_next(now=self.now + timedelta(seconds=2), lease_seconds=1)
        self.store.begin_attempt(work.acquisition_id, work.generation, now=self.now + timedelta(seconds=2), lease_seconds=1)
        self.store.recover_dispatch(child.acquisition_id, now=self.now + timedelta(seconds=4))
        view = self.store.control_view()
        self.assertEqual((view.background_reserved_attempts, view.background_started_attempts), (0, 1))
        self.assertEqual((view.background_reserved_capture_ms, view.background_charged_capture_ms), (0, 125000))
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).background_capture_time_allowance_ms = 249999
        self.assertIsNone(self.store.dispatch_next(now=self.now + timedelta(seconds=4)))
        self.assertEqual(self.store.control_view().background_waiting_reason, "background_capture_time_allowance_exhausted")

    def exclude(self, host="example.com", path_prefix="/"):
        from periplus.crawl.control.collections.frontier_controls import ReplaceFrontierSettings
        current = self.store.control_view()
        settings = current.settings.model_dump(mode="json") | {"exclusions": [{"host": host, "path_prefix": path_prefix}]}
        return self.store.replace_controls(ReplaceFrontierSettings(expected_version=current.policy_version, settings=settings), actor="test")

    def test_exclusion_blocks_new_admission_and_recent_reuse_without_page_charge(self):
        from periplus.crawl.control.collections.exclusions import UrlExcluded
        first = self.admit(self.collection())
        work = self.store.dispatch(first.acquisition_id, now=self.now)
        self.complete(work.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        self.exclude()
        request = self.collection()
        with self.assertRaises(UrlExcluded):
            self.admit(request)
        self.assertEqual(self.store.get_collection(request).consumed, 0)
        self.assertEqual(self.store.get_collection(request).reserved, 0)

    def test_exclusion_releases_queued_interest_even_when_dispatch_is_paused(self):
        request = self.collection()
        own = self.admit(request)
        self.exclude()
        with self.sessions.begin() as session:
            control = session.get(FrontierControlRecord, 1)
            control.paused = True
            control.attempt_allowance = 0
        self.assertEqual(self.store.reconcile_exclusions(), 1)
        self.assertEqual(self.store.reconcile_exclusions(), 0)
        self.assertEqual(self.store.get_interest(own.interest_id).budget_state, "released")
        self.assertEqual(self.store.get_acquisition(own.acquisition_id).terminal_reason, "global_exclusion")
        self.assertEqual(self.store.control_view().pending_acquisitions, 0)

    def test_exclusion_after_dispatch_fences_delivery_and_releases_unstarted_attempt(self):
        request = self.collection()
        own = self.admit(request)
        work = self.store.dispatch(own.acquisition_id, now=self.now)
        self.exclude()
        self.assertFalse(self.store.begin_attempt(own.acquisition_id, work.generation, now=self.now))
        view = self.store.control_view()
        self.assertEqual((view.reserved_attempts, view.reserved_capture_ms, view.dispatched_acquisitions), (0, 0, 0))
        self.assertEqual(self.store.get_collection(request).consumed, 1)
        self.assertEqual(self.store.get_interest(own.interest_id).status, "cancelled")
        self.assertFalse(self.store.begin_attempt(own.acquisition_id, work.generation, now=self.now))

    def test_exclusion_finishes_started_capture_and_cancels_retry_with_attempt_history(self):
        own = self.admit(self.collection())
        work = self.store.dispatch(own.acquisition_id, now=self.now, lease_seconds=1)
        self.store.begin_attempt(own.acquisition_id, work.generation, now=self.now, lease_seconds=1)
        self.exclude()
        self.assertEqual(self.store.reconcile_exclusions(), 0)
        self.store.recover_dispatch(own.acquisition_id, now=self.now + timedelta(seconds=2))
        self.assertEqual(self.store.reconcile_exclusions(), 1)
        acquisition = self.store.get_acquisition(own.acquisition_id)
        self.assertEqual(acquisition.outcome["visit"]["outcome"], "cancelled")
        self.assertEqual(len(acquisition.outcome["attempts"]), 1)
        self.assertEqual(self.store.control_view().charged_capture_ms, 125000)

    def test_exclusions_apply_to_background_admission_and_queued_work(self):
        from periplus.crawl.runtime.background_seen import HistoricalSeenResult, SeenCandidates
        _, parent, _ = self.checked_background(["https://child.example/"])
        self.exclude("child.example")
        with self.sessions.begin() as session:
            session.delete(session.get(BackgroundCheckRecord, parent.acquisition_id))
        candidates = SeenCandidates(urls=("https://child.example/",))
        check = self.store.start_background_check(parent.acquisition_id, candidates, now=self.now)
        check = self.store.finish_background_check(check, HistoricalSeenResult(candidates=candidates,
            seen_urls=(), snapshot=7, query_id="q"), now=self.now)
        result = self.store.admit_background(check, candidates.urls[0], self.policy, now=self.now)
        self.assertEqual((result.status, result.reason), ("declined", "global_exclusion"))
        self.assertEqual(self.store.control_view().pending_acquisitions, 0)

    def test_recent_result_with_excluded_effective_url_cannot_be_reused(self):
        first = self.admit(self.collection())
        work = self.store.dispatch(first.acquisition_id, now=self.now)
        self.complete(work.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        with self.sessions.begin() as session:
            record = session.get(AcquisitionRecord, first.acquisition_id)
            record.outcome = record.outcome | {"visit": record.outcome["visit"] | {"effective_url": "https://blocked.example/private"}}
        self.exclude("blocked.example")
        replacement = self.admit(self.collection())
        self.assertEqual(replacement.mode, "acquired")
        self.assertNotEqual(replacement.acquisition_id, first.acquisition_id)

    def test_capture_start_freezes_latest_exclusions_independently_of_dispatch_policy(self):
        own = self.admit(self.collection())
        work = self.store.dispatch(own.acquisition_id, now=self.now)
        changed = self.exclude("other.example", "/private")
        self.assertTrue(self.store.begin_attempt(own.acquisition_id, work.generation, now=self.now))
        acquisition = self.store.get_acquisition(own.acquisition_id)
        self.assertEqual(acquisition.attempt_exclusion_version, changed.policy_version)
        self.assertEqual(acquisition.attempt_exclusions, [{"host": "other.example", "path_prefix": "/private"}])
        self.assertNotEqual(acquisition.dispatch_policy_version, acquisition.attempt_exclusion_version)
        self.exclude("third.example")
        frozen = self.store.get_acquisition(own.acquisition_id)
        self.assertEqual(frozen.attempt_exclusions, acquisition.attempt_exclusions)

    def test_request_url_digest_collision_fails_without_conflating_urls(self):
        from unittest.mock import patch
        request = self.collection()
        with patch("periplus.crawl.runtime.frontier_store.request_url_key", return_value="0" * 64):
            first = self.admit(request, "https://example.com/first")
            with self.assertRaisesRegex(ValueError, "digest collision"):
                self.admit(request, "https://example.com/second")
        self.assertEqual(self.store.get_collection(request).reserved, 1)
        self.assertEqual(self.store.get_interest(first.interest_id).url, "https://example.com/first")

    def test_seed_selection_advances_excluded_candidates_without_fabricating_interests(self):
        from periplus.crawl.runtime.frontier_selection import process_seed_selection
        request = self.collection(seed_urls=("https://blocked.example/", "https://allowed.example/"))
        self.exclude("blocked.example")
        self.assertEqual(process_seed_selection(self.store, request, lambda url: self.policy), "settled")
        with self.sessions() as session:
            rows = list(session.scalars(select(InterestRecord).where(InterestRecord.collection_id == request)))
        self.assertEqual([row.url for row in rows], ["https://allowed.example/"])
        self.assertEqual(self.store.get_collection(request).reserved, 1)
        self.assertTrue(self.store.get_collection(request).seeds_settled)

    def test_excluded_checkpoint_cannot_advance_if_policy_changes_before_commit(self):
        from periplus.crawl.runtime.selection_contract import SelectionCheckpoint
        request = self.collection()
        self.store.freeze_selection(request, SelectionCheckpoint(urls=("https://example.com/",)), seeds=True)
        self.exclude()
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).exclusions = []
        self.assertFalse(self.store.advance_selection(request, 0, seeds=True, excluded=True))
        self.assertEqual(self.store.get_collection(request).selection_checkpoint["cursor"], 0)
        with self.assertRaisesRegex(ValueError, "unaccounted"):
            self.store.advance_selection(request, 0, seeds=True)
        self.admit(request)
        self.assertTrue(self.store.advance_selection(request, 0, seeds=True))

    def test_evicted_navigation_prevents_branch_reuse_even_when_json_null_is_stored(self):
        first = self.admit(self.collection())
        work = self.store.dispatch(first.acquisition_id, now=self.now)
        self.complete(work.acquisition_id, work.generation, success=True, outcome={}, now=self.now,
                      navigation=navigation_package())
        with self.sessions.begin() as session:
            session.get(AcquisitionRecord, first.acquisition_id).navigation = None
        branch = self.admit(self.collection(max_depth=1))
        self.assertNotEqual(branch.acquisition_id, first.acquisition_id)
        leaf = self.admit(self.collection())
        self.assertEqual(leaf.acquisition_id, first.acquisition_id)
        self.assertEqual(leaf.mode, "reused")

    def retention_parent(self):
        own = self.admit(self.collection(max_depth=1))
        package = navigation_package().model_copy(update={
            "object_name": f"runtime/navigation/{own.acquisition_id.hex}/{'a' * 64}.arrow"})
        work = self.store.dispatch(own.acquisition_id, now=self.now)
        self.complete(own.acquisition_id, work.generation, success=True, outcome={}, now=self.now, navigation=package)
        return own, package

    def allow_retention_receipts(self, identity):
        with self.sessions.begin() as session:
            session.get(AcquisitionRecord, identity).evidence_snapshot = 7
            for row in session.scalars(select(FrontierOutboxRecord).where(
                    FrontierOutboxRecord.acquisition_id == identity, FrontierOutboxRecord.kind.in_(("observation", "lineage")))):
                row.committed_snapshot = 7

    def retire(self, identity, key):
        return self.store.retire_navigation(identity, key, modified_at=self.now,
            cutoff=self.now + timedelta(hours=3), orphan_cutoff=self.now + timedelta(hours=1))

    def test_navigation_retirement_waits_for_receipts_and_request_selection(self):
        own, package = self.retention_parent()
        self.assertFalse(self.retire(own.acquisition_id, package.object_name))
        self.allow_retention_receipts(own.acquisition_id)
        self.assertFalse(self.retire(own.acquisition_id, package.object_name))
        self.store.finish_link_selection(own.interest_id)
        self.assertTrue(self.retire(own.acquisition_id, package.object_name))
        acquisition = self.store.get_acquisition(own.acquisition_id)
        self.assertIsNone(acquisition.navigation)
        self.assertEqual(acquisition.retired_navigation, package.model_dump(mode="json"))
        self.assertTrue(self.retire(own.acquisition_id, package.object_name))
        self.assertEqual(acquisition.evidence_snapshot, 7)

    def test_background_allocation_and_active_check_pin_navigation(self):
        from periplus.crawl.runtime.background_seen import SeenCandidates
        own, package = self.retention_parent()
        self.store.finish_link_selection(own.interest_id)
        self.allow_retention_receipts(own.acquisition_id)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).background_share = 10
        self.assertFalse(self.retire(own.acquisition_id, package.object_name))
        check = self.store.start_background_check(own.acquisition_id, SeenCandidates(urls=("https://child.example/",)))
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).background_share = 0
        self.assertFalse(self.retire(own.acquisition_id, package.object_name))
        with self.sessions.begin() as session:
            session.get(BackgroundCheckRecord, own.acquisition_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        self.assertTrue(self.retire(own.acquisition_id, package.object_name))
        self.assertIsNotNone(self.store.get_acquisition(own.acquisition_id).outcome)

    def test_retired_navigation_preserves_exact_completion_replay_and_forces_branch_capture(self):
        from periplus.platform.catalogue.records import VisitEvidence
        own, package = self.retention_parent()
        self.store.finish_link_selection(own.interest_id)
        self.allow_retention_receipts(own.acquisition_id)
        before = self.store.get_acquisition(own.acquisition_id)
        self.assertTrue(self.retire(own.acquisition_id, package.object_name))
        self.assertFalse(self.store.complete(own.acquisition_id, before.generation,
            evidence=VisitEvidence.model_validate(before.outcome), navigation=package, now=self.now))
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.store.complete(own.acquisition_id, before.generation,
                evidence=VisitEvidence.model_validate(before.outcome), navigation=package.model_copy(update={"row_count": 999}), now=self.now)
        branch = self.admit(self.collection(max_depth=1))
        self.assertNotEqual(branch.acquisition_id, own.acquisition_id)
        self.assertEqual(self.admit(self.collection()).acquisition_id, own.acquisition_id)

    def test_navigation_cleanup_rejects_unowned_paths_and_recent_or_live_objects(self):
        identity = uuid4()
        key = f"runtime/navigation/{identity.hex}/{'a' * 64}.arrow"
        self.assertTrue(self.retire(identity, key))
        self.assertFalse(self.retire(identity, f"runtime/navigation/{identity.hex}/../../raw.html"))
        self.assertFalse(self.store.retire_navigation(identity, key, modified_at=self.now,
            cutoff=self.now + timedelta(hours=1), orphan_cutoff=self.now - timedelta(seconds=1)))
        own = self.admit(self.collection())
        self.assertFalse(self.retire(own.acquisition_id, f"runtime/navigation/{own.acquisition_id.hex}/{'a' * 64}.arrow"))

    def test_retained_acquisition_limit_counts_terminal_results_but_allows_sharing_and_reuse(self):
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).acquisition_limit = 1
        first = self.admit(self.collection())
        shared = self.admit(self.collection())
        self.assertEqual(first.acquisition_id, shared.acquisition_id)
        with self.assertRaisesRegex(AdmissionDeferred, "retained acquisition"):
            self.admit(self.collection(), "https://other.example/")
        work = self.store.dispatch(first.acquisition_id, now=self.now)
        self.complete(first.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        self.assertEqual(self.admit(self.collection()).mode, "reused")
        with self.assertRaisesRegex(AdmissionDeferred, "retained acquisition"):
            self.admit(self.collection(), "https://other.example/")
        view = self.store.control_view()
        self.assertEqual(view.retained_acquisitions, 1)
        self.assertEqual(view.pending_acquisitions, 0)
        self.assertEqual(view.acquisition_admission_waiting_reason, "retained_acquisition_capacity")

    def test_retained_capacity_bounds_paused_interest_split_without_consuming_request_pages(self):
        first, second = self.collection(), self.collection()
        own = self.admit(first)
        self.admit(second)
        self.store.set_collection_paused(first, True)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).acquisition_limit = 1
        self.assertIsNone(self.store.dispatch(own.acquisition_id, now=self.now))
        self.assertEqual(self.store.get_collection(second).consumed, 0)
        self.assertEqual(self.store.get_collection(second).reserved, 1)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).acquisition_limit = 2
        self.assertIsNotNone(self.store.dispatch(own.acquisition_id, now=self.now))
        self.assertEqual(self.store.control_view().retained_acquisitions, 2)
        self.assertEqual(self.store.get_collection(first).reserved, 1)
        self.assertEqual(self.store.get_collection(second).consumed, 1)

    def test_retained_capacity_defers_background_without_recording_a_completed_decision(self):
        _, parent, check = self.checked_background(["https://child.example/"])
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).acquisition_limit = 1
        with self.assertRaisesRegex(AdmissionDeferred, "retained acquisition"):
            self.store.admit_background(check, "https://child.example/", self.policy, now=self.now)
        self.assertFalse(self.store.get_acquisition(parent.acquisition_id).background_selected)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).acquisition_limit = 2
        result = self.store.admit_background(check, "https://child.example/", self.policy, now=self.now)
        self.assertEqual(result.status, "admitted")

    def background_terminal(self):
        _, parent, check = self.checked_background(["https://cleanup.example/"])
        child = self.store.admit_background(check, "https://cleanup.example/", self.policy, now=self.now)
        work = self.store.dispatch(child.acquisition_id, now=self.now)
        self.complete(child.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        return child

    def test_acquisition_cleanup_requires_all_receipts_and_removes_only_unreferenced_records(self):
        child = self.background_terminal()
        cutoff = self.now + timedelta(seconds=1)
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff), 0)
        with self.sessions.begin() as session:
            session.get(AcquisitionRecord, child.acquisition_id).evidence_snapshot = 7
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff), 0)
        self.allow_retention_receipts(child.acquisition_id)
        before = self.store.control_view()
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff), 1)
        self.assertIsNone(self.store.get_acquisition(child.acquisition_id))
        after = self.store.control_view()
        self.assertEqual(after.retained_acquisitions, before.retained_acquisitions - 1)
        self.assertEqual(after.started_attempts, before.started_attempts)
        with self.sessions() as session:
            self.assertEqual(list(session.scalars(select(FrontierOutboxRecord).where(FrontierOutboxRecord.acquisition_id == child.acquisition_id))), [])
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff), 0)

    def test_acquisition_cleanup_keeps_settled_request_references(self):
        child = self.background_terminal()
        own = self.admit(self.collection(), child.url)
        self.assertEqual(own.mode, "reused")
        self.store.finish_link_selection(own.interest_id)
        self.allow_retention_receipts(child.acquisition_id)
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=self.now + timedelta(seconds=1)), 0)
        self.assertIsNotNone(self.store.get_acquisition(child.acquisition_id))

    def test_cleanup_watermark_blocks_old_checks_and_lake_seen_decision_survives_deletion(self):
        from periplus.crawl.runtime.background_seen import HistoricalSeenResult, SeenCandidates
        child = self.background_terminal()
        self.allow_retention_receipts(child.acquisition_id)
        parent, _ = self.retention_parent()
        candidates = SeenCandidates(urls=(child.url,))
        check = self.store.start_background_check(parent.acquisition_id, candidates)
        cutoff = self.now + timedelta(seconds=1)
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff), 0)
        check = self.store.finish_background_check(check, HistoricalSeenResult(candidates=candidates,
            seen_urls=(), snapshot=6, query_id="old"))
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff), 0)
        with self.sessions.begin() as session:
            session.get(BackgroundCheckRecord, parent.acquisition_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        fresh = self.store.start_background_check(parent.acquisition_id, candidates)
        fresh = self.store.finish_background_check(fresh, HistoricalSeenResult(candidates=candidates,
            seen_urls=(child.url,), snapshot=7, query_id="fresh"))
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff), 1)
        self.assertEqual(self.store.admit_background(fresh, child.url, self.policy).status, "seen")
        self.assertIsNone(self.store.get_acquisition(child.acquisition_id))

    def test_retention_cursor_progresses_beyond_first_uncommitted_batch(self):
        from uuid import UUID
        with self.sessions.begin() as session:
            for index in range(1, 66):
                session.add(AcquisitionRecord(id=UUID(int=index), url=f"https://retained.example/{index}",
                    domain="retained.example", capture_key=str(index), requirements=self.policy.model_dump(mode="json"),
                    visibility="public", access_context="public", status="failed" if index <= 64 else "cancelled",
                    completed_at=self.now))
        cutoff = self.now + timedelta(seconds=1)
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff), 0)
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff), 1)
        self.assertIsNone(self.store.get_acquisition(UUID(int=65)))
        self.assertEqual(self.store.control_view().retained_acquisitions, 64)

    def commit_collection_receipts(self, identity):
        with self.sessions.begin() as session:
            for row in session.scalars(select(FrontierOutboxRecord).where(FrontierOutboxRecord.collection_id == identity)):
                row.committed_snapshot = 7

    def test_collection_cleanup_requires_definition_outcome_and_supplied_evidence_receipts(self):
        identity = self.collection()
        own = self.admit(identity)
        work = self.store.dispatch(own.acquisition_id, now=self.now)
        self.complete(own.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        self.store.finish_link_selection(own.interest_id)
        self.store.finish_seed_selection(identity)
        self.store.settle_collection(identity, now=self.now)
        cutoff = self.now + timedelta(seconds=1)
        self.assertEqual(self.store.cleanup_collections(cutoff=cutoff), 0)
        self.commit_collection_receipts(identity)
        self.assertEqual(self.store.cleanup_collections(cutoff=cutoff), 0)
        self.allow_retention_receipts(own.acquisition_id)
        self.assertEqual(self.store.cleanup_collections(cutoff=cutoff), 1)
        self.assertIsNone(self.store.get_collection(identity))
        self.assertEqual(self.store.control_view().retained_interests, 0)
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff), 1)
        self.assertEqual(self.store.control_view().retained_acquisitions, 0)

    def test_collection_cleanup_prunes_bounded_rows_and_hides_partial_current_counts(self):
        from periplus.crawl.runtime.frontier_store import request_url_key
        from periplus.crawl.runtime.frontier_views import collection_views
        identity = self.collection(page_limit=1000)
        with self.sessions.begin() as session:
            for index in range(600):
                aid = uuid4()
                url = f"https://example.com/{index}"
                session.add(AcquisitionRecord(id=aid, url=url, domain="example.com", capture_key=str(aid),
                    requirements=self.policy.model_dump(mode="json"), visibility="public", access_context="public",
                    status="cancelled", completed_at=self.now))
                session.add(InterestRecord(collection_id=identity, acquisition_id=aid, url=url, url_key=request_url_key(url),
                    context=self.context.model_dump(mode="json"), mode="acquired", budget_state="released", status="cancelled"))
            session.get(FrontierControlRecord, 1).interest_count = 600
        self.store.stop_collection(identity, now=self.now)
        self.commit_collection_receipts(identity)
        cutoff = self.now + timedelta(seconds=1)
        self.assertEqual(self.store.cleanup_collections(cutoff=cutoff), 0)
        self.assertEqual(self.store.control_view().retained_interests, 88)
        self.assertTrue(self.store.get_collection(identity).retiring)
        self.assertEqual(collection_views(self.sessions, identity=identity), [])
        for _ in range(3):
            self.store.cleanup_collections(cutoff=cutoff)
        self.assertIsNone(self.store.get_collection(identity))
        self.assertEqual(self.store.control_view().retained_interests, 0)

    def test_collection_cleanup_never_assumes_missing_history_outbox_is_committed(self):
        identity = self.collection()
        self.store.stop_collection(identity, now=self.now)
        self.commit_collection_receipts(identity)
        with self.sessions.begin() as session:
            session.delete(session.get(FrontierOutboxRecord, f"lineage:collection:{identity}"))
        self.assertEqual(self.store.cleanup_collections(cutoff=self.now + timedelta(seconds=1)), 0)
        self.assertFalse(self.store.get_collection(identity).retiring)
