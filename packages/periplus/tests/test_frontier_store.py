"""Behavioral checks for shared acquisition and once-per-request accounting."""
from capture_policy_fixture import capture_policy
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
    AcquisitionRecord, FrontierControlRecord, FrontierOutboxRecord, InterestRecord,
)
from periplus.crawl.runtime.frontier_store import (
    CollectionUnavailable, FrontierStore, StaleDispatch,
)

from periplus.crawl.control.domain_policies.models import DomainPolicy

TABLES = (DomainPolicy.__table__, CollectionRecord.__table__, FrontierControlRecord.__table__,
          AcquisitionRecord.__table__, InterestRecord.__table__, FrontierOutboxRecord.__table__)


class FrontierStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        for table in TABLES:
            table.create(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(FrontierControlRecord(id=1))
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
                                                              request_class='admin'))
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
                self.assertEqual(control.active_count, 0)
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
            self.assertEqual(control.active_count, 0)

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
            capture_policy=capture_policy(),
            visit_id=acquisition_id, requested_url=acquisition.url,
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
            self.assertEqual((control.active_count, control.pending_count), (0, 3))
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

    def test_all_request_classes_share_but_capture_requirements_do_not(self):
        public = self.admit(self.collection(request_class="public"))
        private_a = self.admit(self.collection(request_class='admin'))
        private_b = self.admit(self.collection(request_class="system"))
        self.assertEqual(len({public.acquisition_id, private_a.acquisition_id,
                              private_b.acquisition_id}), 1)
        other = self.store.admit(self.collection(), "https://example.com/", self.context,
                                EffectivePolicySnapshot.model_validate(policy_snapshot()), now=self.now)
        self.assertNotEqual(public.acquisition_id, other.acquisition_id)

    def test_admission_independent_of_dispatch_capacity(self):
        with self.sessions.begin() as session:
            control = session.get(FrontierControlRecord, 1)
            control.dispatch_limit = 1
        first = self.admit(self.collection())
        self.store.dispatch(first.acquisition_id, now=self.now)
        for index in range(3):
            item = self.admit(self.collection(), f"https://example.com/{index}")
            self.assertIsNone(self.store.dispatch(item.acquisition_id, now=self.now))
        self.assertTrue(self.admit(self.collection(), "https://example.com/overflow").created)
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
            capture_policy=capture_policy(),
            visit_id=uuid4(), requested_url="https://example.com/",
            admitted_at=self.now, finished_at=self.now, outcome="failed",
        ), attempts=())
        with self.assertRaisesRegex(ValueError, "different acquisition"):
            self.store.complete(a.acquisition_id, work.generation, evidence=wrong, now=self.now)
        self.complete(a.acquisition_id, work.generation, success=True, outcome={},
                      navigation=navigation_package(), now=self.now)
        with self.assertRaisesRegex(ValueError, "conflicting terminal outcome"):
            self.complete(a.acquisition_id, work.generation, success=True, outcome={}, now=self.now)

    def test_outcome_cannot_change_acquisition_url(self):
        from periplus.crawl.runtime.frontier_evidence import terminal_evidence
        a = self.admit(self.collection(request_class='admin'))
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        evidence = terminal_evidence(self.store.get_acquisition(a.acquisition_id), self.now, "failed")
        self.assertEqual(evidence.visit.capture_policy.model_dump(mode="json"),
                         self.store.get_acquisition(a.acquisition_id).requirements["content"])
        for change in ({"requested_url": "https://other.example/"},):
            wrong = evidence.model_copy(update={"visit": evidence.visit.model_copy(update=change)})
            with self.assertRaisesRegex(ValueError, "URL"):
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
        policy = lambda url: self.policy
        self.assertEqual(process_seed_selection(self.store, identity, policy, seed_query=query), "settled")
        checkpoint = self.store.get_collection(identity).seed_provenance
        from periplus.query.service import QueryRequest
        query.assert_called_once_with(QueryRequest(
            sql="SELECT requested_url AS url FROM web.observation WHERE outcome = ?", parameters=["succeeded"]))
        self.assertEqual(checkpoint["source_query_id"], "query-17")
        self.assertEqual(checkpoint["selected_at"], self.now.isoformat().replace("+00:00", "Z"))
        self.assertEqual(self.store.get_collection(identity).reserved, 3)
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
                                   max_duration_seconds=1)
        with self.sessions.begin() as session:
            record = session.get(CollectionRecord, identity)
            record.spec = record.spec | {"deadline_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
        query = Mock()
        self.assertEqual(process_seed_selection(self.store, identity, lambda url: self.policy, seed_query=query), "settled")
        query.assert_not_called()
        self.assertEqual(self.store.get_collection(identity).outcome, "duration_limit")

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
        identity = self.collection(max_duration_seconds=1)
        with self.sessions.begin() as session:
            record = session.get(CollectionRecord, identity)
            record.spec = record.spec | {"deadline_at": (self.now + timedelta(seconds=1)).isoformat()}
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

    def test_shared_admission_preserves_request_dedup(self):
        identity = self.collection(page_limit=3)
        first = self.admit(identity)
        self.assertFalse(self.admit(identity).created)
        self.assertEqual(self.admit(self.collection()).mode, "shared")
        self.store.stop_collection(identity, now=self.now)
        # Cancelled interests stay deduplicated until acknowledged lineage cleanup.
        self.assertEqual(self.admit(identity).interest_id, first.interest_id)
        with self.sessions() as session:
            self.assertEqual(session.get(FrontierControlRecord, 1).interest_count, 2)

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
            settings=initial.settings.model_copy(update={"paused": True}),
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
        second = self.admit(self.collection(), "https://other.example/")
        self.assertIsNotNone(self.store.dispatch(second.acquisition_id, now=self.now))

    def test_shared_dispatch_consumes_each_request_once_and_releases_capacity(self):
        from periplus.crawl.control.collections.frontier_controls import ReplaceFrontierSettings
        first, second = self.collection(), self.collection()
        a, b = self.admit(first), self.admit(second)
        initial = self.store.control_view()
        self.store.replace_controls(ReplaceFrontierSettings(
            expected_version=initial.policy_version,
            settings=initial.settings.model_copy(update={"dispatch_limit": 1}),
        ), actor="test", now=self.now)
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        self.assertEqual(a.acquisition_id, b.acquisition_id)
        self.assertEqual(self.store.control_view().dispatch_waiting_reason, "dispatch_capacity")
        other = self.admit(self.collection(), "https://other.example/")
        self.assertIsNone(self.store.dispatch(other.acquisition_id, now=self.now + timedelta(seconds=1)))
        self.assertEqual(self.store.get_interest(other.interest_id).budget_state, "reserved")
        self.complete(work.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        self.assertFalse(self.complete(work.acquisition_id, work.generation, success=True, outcome={}, now=self.now))
        self.assertEqual(self.store.get_collection(first).consumed, 1)
        self.assertEqual(self.store.get_collection(second).consumed, 1)
        self.assertIsNotNone(self.store.dispatch(other.acquisition_id, now=self.now + timedelta(seconds=2)))

    def test_unstarted_dispatch_releases_slot_and_uncertain_start_preserves_evidence(self):
        identity = self.collection()
        a = self.admit(identity)
        work = self.store.dispatch(a.acquisition_id, now=self.now, lease_seconds=1)
        self.store.recover_dispatch(a.acquisition_id, now=self.now + timedelta(seconds=2))
        self.assertEqual(self.store.control_view().dispatched_acquisitions, 0)
        retry = self.store.dispatch(a.acquisition_id, now=self.now + timedelta(seconds=2), lease_seconds=1)
        self.store.begin_attempt(a.acquisition_id, retry.generation, now=self.now + timedelta(seconds=2), lease_seconds=1)
        self.store.recover_dispatch(a.acquisition_id, now=self.now + timedelta(seconds=4))
        self.assertEqual(self.store.control_view().dispatched_acquisitions, 0)
        self.store.recover_dispatch(a.acquisition_id, now=self.now + timedelta(seconds=5))
        self.store.stop_collection(identity, now=self.now + timedelta(seconds=5))
        evidence = self.store.get_acquisition(a.acquisition_id).outcome
        self.assertEqual(evidence["attempts"][0]["resource_usage"]["reserved_ms"], 125000)
        self.assertIsNone(evidence["attempts"][0]["resource_usage"]["measured_ms"])

    def test_controls_reject_removed_limits(self):
        from pydantic import ValidationError
        from periplus.crawl.control.collections.frontier_controls import FrontierSettings
        settings = self.store.control_view().settings.model_dump()
        for field in ('attempt_allowance', 'capture_time_allowance_ms', 'collection_limit', 'interest_limit', 'acquisition_limit', 'admission_limit', 'captures_per_minute'):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                FrontierSettings.model_validate(settings | {field: 0})
        view = self.store.control_view().model_dump()
        self.assertTrue({'reserved_attempts', 'started_attempts', 'reserved_capture_ms',
                         'charged_capture_ms', 'allowance_semantics', 'time_semantics'}.isdisjoint(view))

    def test_successive_captures_have_no_lifetime_budget(self):
        # Every completed attempt releases its slot for the next acquisition.
        for index in range(5):
            now = self.now + timedelta(seconds=index * 2)
            a = self.admit(self.collection(), f"https://example.com/{index}")
            work = self.store.dispatch(a.acquisition_id, now=now)
            self.assertIsNotNone(work)
            self.complete(a.acquisition_id, work.generation, success=True, outcome={}, now=now)
            self.assertEqual(self.store.control_view().dispatched_acquisitions, 0)

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
            capture_policy=capture_policy(),
            visit_id=a.acquisition_id, requested_url="https://example.com/", admitted_at=self.now,
            started_at=self.now, finished_at=self.now, outcome="succeeded",
        ), attempts=(attempt,))
        wrong = evidence.model_copy(update={"attempts": (attempt.model_copy(update={
            "resource_usage": attempt.resource_usage.model_copy(update={"reserved_ms": 1}),
        }),)})
        with self.assertRaisesRegex(ValueError, "frozen reservation"):
            self.store.complete(a.acquisition_id, work.generation, evidence=wrong, now=self.now)
        self.store.complete(a.acquisition_id, work.generation, evidence=evidence, now=self.now)
        self.assertEqual(self.store.get_acquisition(a.acquisition_id).outcome["attempts"][0]["resource_usage"]["measured_ms"], 130000)

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
            capture_policy=capture_policy(),
            visit_id=a.acquisition_id, requested_url="https://example.com/",
            admitted_at=self.now, started_at=self.now, finished_at=later, outcome="succeeded",
        ), attempts=attempts)
        changed = evidence.model_copy(update={"attempts": (attempts[0].model_copy(update={
            "resource_usage": attempts[0].resource_usage.model_copy(update={"measured_ms": 0}),
        }), attempts[1])})
        with self.assertRaisesRegex(ValueError, "previously recorded"):
            self.store.complete(a.acquisition_id, second.generation, evidence=changed, now=later)
        self.store.complete(a.acquisition_id, second.generation, evidence=evidence, now=later)
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
        self.assertEqual(self.store.control_view().dispatched_acquisitions, 0)
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

    def test_completed_work_does_not_block_fresh_admission(self):
        first = self.admit(self.collection())
        shared = self.admit(self.collection())
        self.assertEqual(first.acquisition_id, shared.acquisition_id)
        work = self.store.dispatch(first.acquisition_id, now=self.now)
        self.complete(first.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        self.assertEqual(self.admit(self.collection()).mode, "reused")
        self.assertTrue(self.admit(self.collection(), "https://other.example/").created)
        self.assertEqual(self.store.control_view().retained_acquisitions, 2)

    def test_paused_interest_split_does_not_block_other_requests(self):
        first, second = self.collection(), self.collection()
        own = self.admit(first)
        self.admit(second)
        self.store.set_collection_paused(first, True)
        self.assertIsNotNone(self.store.dispatch(own.acquisition_id, now=self.now))
        self.assertEqual(self.store.control_view().retained_acquisitions, 2)
        self.assertEqual(self.store.get_collection(first).reserved, 1)
        self.assertEqual(self.store.get_collection(second).consumed, 1)

    def test_retention_cursor_progresses_beyond_first_uncommitted_batch(self):
        from uuid import UUID
        with self.sessions.begin() as session:
            for index in range(1, 66):
                session.add(AcquisitionRecord(id=UUID(int=index), url=f"https://retained.example/{index}",
                    domain="retained.example", capture_key=str(index), requirements=self.policy.model_dump(mode="json"),
                      status="failed" if index <= 64 else "cancelled",
                    completed_at=self.now))
        cutoff = self.now + timedelta(seconds=1)
        first = self.store.cleanup_acquisitions(cutoff=cutoff)
        self.assertEqual(first.removed, 0)
        self.assertTrue(first.more)
        last = self.store.cleanup_acquisitions(cutoff=cutoff)
        self.assertEqual(last.removed, 1)
        self.assertFalse(last.more)
        self.assertIsNone(self.store.get_acquisition(UUID(int=65)))
        self.assertEqual(self.store.control_view().retained_acquisitions, 64)

    def test_acquisition_cleanup_drains_multiple_transactions_and_finishes(self):
        from uuid import UUID
        with self.sessions.begin() as session:
            for index in range(1, 194):
                session.add(AcquisitionRecord(id=UUID(int=index), url=f"https://retired.example/{index}",
                    domain="retired.example", capture_key=str(index), requirements=self.policy.model_dump(mode="json"),
                    status="cancelled", completed_at=self.now))
        results = [self.store.cleanup_acquisitions(cutoff=self.now + timedelta(seconds=1)) for _ in range(4)]
        self.assertEqual([batch.removed for batch in results], [64, 64, 64, 1])
        self.assertEqual([batch.more for batch in results], [True, True, True, False])
        self.assertEqual(self.store.control_view().retained_acquisitions, 0)

    def commit_collection_receipts(self, identity):
        with self.sessions.begin() as session:
            for row in session.scalars(select(FrontierOutboxRecord).where(FrontierOutboxRecord.collection_id == identity)):
                row.committed_snapshot = 7

    def test_completed_acquisition_reclaimed_while_parent_remains_active(self):
        from periplus.crawl.runtime.frontier_views import collection_views
        identity = self.collection(page_limit=3)
        own = self.admit(identity)
        shared = self.admit(self.collection())
        pending = self.admit(identity, "https://example.com/next")
        work = self.store.dispatch(own.acquisition_id, now=self.now)
        self.complete(own.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        cutoff = self.now + timedelta(hours=1)
        # Ingestion and selection must both finish before the payload can go.
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff).removed, 0)
        self.allow_retention_receipts(own.acquisition_id)
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff).removed, 0)
        self.store.finish_link_selection(own.interest_id)
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff).removed, 0)
        self.store.finish_link_selection(shared.interest_id)
        before, = collection_views(self.sessions, identity=identity)
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff).removed, 1)
        self.assertIsNone(self.store.get_acquisition(own.acquisition_id))
        self.assertIsNotNone(self.store.get_acquisition(pending.acquisition_id))
        interest = self.store.get_interest(own.interest_id)
        self.assertIsNone(interest.acquisition_id)
        self.assertIsNone(interest.context)
        after, = collection_views(self.sessions, identity=identity)
        for field in ('consumed_pages', 'supplied_pages', 'failed_pages', 'shared_pages', 'reused_pages', 'ingested_pages', 'queued_pages'):
            self.assertEqual(getattr(after, field), getattr(before, field), field)
        self.assertEqual(after.status, 'active')
        self.assertFalse(self.admit(identity).created)
        self.assertEqual(self.store.get_collection(identity).consumed, 1)
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff).removed, 0)

        self.store.stop_collection(identity, now=self.now + timedelta(hours=2))
        with self.sessions() as session:
            outcome = session.get(FrontierOutboxRecord, f"lineage:collection_outcome:{identity}").payload
            self.assertEqual(outcome['supplied_pages'], 1)
            self.assertEqual(outcome['failed_pages'], 0)

    def test_shared_completion_compaction_is_bounded_and_resumable(self):
        from periplus.crawl.runtime.frontier_store import request_url_key
        identity = self.collection()
        aid = uuid4()
        with self.sessions.begin() as session:
            session.add(AcquisitionRecord(id=aid, url='https://example.com/', domain='example.com',
                capture_key=str(aid), requirements={}, status='cancelled', completed_at=self.now))
            for index in range(600):
                url = f'https://example.com/{index}'
                session.add(InterestRecord(collection_id=identity, acquisition_id=aid, url=url,
                    url_key=request_url_key(url), context={}, mode='shared', budget_state='released', status='cancelled'))
            session.get(FrontierControlRecord, 1).interest_count = 600
        first = self.store.cleanup_acquisitions(cutoff=self.now + timedelta(seconds=1))
        self.assertTrue(first.more)
        self.assertEqual(first.removed, 0)
        with self.sessions() as session:
            self.assertEqual(len(list(session.scalars(select(InterestRecord).where(InterestRecord.acquisition_id.is_(None))))), 512)
        second = self.store.cleanup_acquisitions(cutoff=self.now + timedelta(seconds=1))
        self.assertFalse(second.more)
        self.assertEqual(second.removed, 1)
        self.assertEqual(self.store.control_view().retained_interests, 600)

    def test_acquisition_outbox_cleanup_is_bounded_and_resumable(self):
        aid = uuid4()
        with self.sessions.begin() as session:
            session.add(AcquisitionRecord(id=aid, url='https://example.com/', domain='example.com',
                capture_key=str(aid), requirements={}, status='cancelled', completed_at=self.now))
            for index in range(600):
                session.add(FrontierOutboxRecord(message_id=f'lineage:test:{index}', acquisition_id=aid,
                    kind='lineage', payload={}, committed_snapshot=7))
        first = self.store.cleanup_acquisitions(cutoff=self.now + timedelta(seconds=1))
        self.assertEqual((first.removed, first.more), (0, True))
        second = self.store.cleanup_acquisitions(cutoff=self.now + timedelta(seconds=1))
        self.assertEqual((second.removed, second.more), (1, False))

    def test_collection_cleanup_requires_definition_outcome_and_supplied_evidence_receipts(self):
        identity = self.collection()
        own = self.admit(identity)
        work = self.store.dispatch(own.acquisition_id, now=self.now)
        self.complete(own.acquisition_id, work.generation, success=True, outcome={}, now=self.now)
        self.store.finish_link_selection(own.interest_id)
        self.store.finish_seed_selection(identity)
        self.store.settle_collection(identity, now=self.now)
        cutoff = self.now + timedelta(seconds=1)
        self.assertEqual(self.store.cleanup_collections(cutoff=cutoff).removed, 0)
        self.commit_collection_receipts(identity)
        self.assertEqual(self.store.cleanup_collections(cutoff=cutoff).removed, 0)
        self.allow_retention_receipts(own.acquisition_id)
        self.assertEqual(self.store.cleanup_collections(cutoff=cutoff).removed, 1)
        self.assertIsNone(self.store.get_collection(identity))
        self.assertEqual(self.store.control_view().retained_interests, 0)
        self.assertEqual(self.store.cleanup_acquisitions(cutoff=cutoff).removed, 1)
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
                    requirements=self.policy.model_dump(mode="json"),
                    status="cancelled", completed_at=self.now))
                session.add(InterestRecord(collection_id=identity, acquisition_id=aid, url=url, url_key=request_url_key(url),
                    context=self.context.model_dump(mode="json"), mode="acquired", budget_state="released", status="cancelled"))
            session.get(FrontierControlRecord, 1).interest_count = 600
        self.store.stop_collection(identity, now=self.now)
        self.commit_collection_receipts(identity)
        cutoff = self.now + timedelta(seconds=1)
        self.assertEqual(self.store.cleanup_collections(cutoff=cutoff).removed, 0)
        self.assertEqual(self.store.control_view().retained_interests, 88)
        self.assertTrue(self.store.get_collection(identity).retiring)
        self.assertEqual(collection_views(self.sessions, identity=identity), [])
        final = self.store.cleanup_collections(cutoff=cutoff)
        self.assertEqual(final.removed, 1)
        self.assertFalse(final.more)
        self.assertIsNone(self.store.get_collection(identity))
        self.assertEqual(self.store.control_view().retained_interests, 0)

    def test_collection_cleanup_never_assumes_missing_history_outbox_is_committed(self):
        identity = self.collection()
        self.store.stop_collection(identity, now=self.now)
        self.commit_collection_receipts(identity)
        with self.sessions.begin() as session:
            session.delete(session.get(FrontierOutboxRecord, f"lineage:collection:{identity}"))
        self.assertEqual(self.store.cleanup_collections(cutoff=self.now + timedelta(seconds=1)).removed, 0)
        self.assertFalse(self.store.get_collection(identity).retiring)

    def test_duration_expiry_keeps_started_capture_and_blocks_remaining_work(self):
        identity = self.collection(page_limit=3, max_duration_seconds=60)
        a = self.admit(identity)
        pending = self.admit(identity, "https://example.com/next")
        work = self.store.dispatch(a.acquisition_id, now=self.now)
        self.assertTrue(self.store.begin_attempt(a.acquisition_id, work.generation, now=self.now))
        self.store.stop_collection(identity, reason="duration_limit", now=self.now + timedelta(seconds=61))
        self.assertEqual(self.store.get_collection(identity).status, "active")
        self.assertEqual(self.store.get_acquisition(pending.acquisition_id).status, "cancelled")
        self.assertEqual(self.store.get_interest(a.interest_id).status, "awaiting_result")
        self.complete(a.acquisition_id, work.generation, success=True, outcome={}, now=self.now + timedelta(seconds=62))
        self.store.stop_collection(identity, reason="duration_limit", now=self.now + timedelta(seconds=63))
        self.assertEqual(self.store.get_collection(identity).outcome, "duration_limit")
        self.assertEqual(self.store.get_collection(identity).status, "settled")
        with self.sessions() as session:
            self.assertIsNotNone(session.get(FrontierOutboxRecord, f"lineage:fulfillment:{a.interest_id}"))
