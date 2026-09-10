"""Opt-in transaction races against isolated schemas in configured control Postgres."""
from capture_policy_fixture import capture_policy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import os
from threading import Barrier
import unittest
from uuid import uuid4

from periplus.crawl.control.domain_policies.service import ensure_default_domain_policy

from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from frontier_fixtures import policy_snapshot
from test_frontier_store import TABLES
from periplus.crawl.control.collections.schemas import CollectionSpec, SelectionContext
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from periplus.crawl.runtime.frontier_models import AcquisitionRecord, FrontierControlRecord, InterestRecord
from periplus.crawl.runtime.frontier_store import FrontierStore


@unittest.skipUnless(os.environ.get("PERIPLUS_TEST_FRONTIER_POSTGRES") == "1",
                     "set PERIPLUS_TEST_FRONTIER_POSTGRES=1 for live Postgres races")
class FrontierPostgresTests(unittest.TestCase):
    def setUp(self):
        from periplus.platform.postgres.session import get_engine
        self.base_engine = get_engine()
        self.schema = "frontier_test_" + uuid4().hex
        with self.base_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = self.base_engine.execution_options(schema_translate_map={None: self.schema})
        for table in TABLES:
            table.create(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(FrontierControlRecord(id=1))
            ensure_default_domain_policy(session)
        self.store = FrontierStore(self.sessions)
        self.policy = EffectivePolicySnapshot.model_validate(policy_snapshot())
        self.context = SelectionContext(depth=0, rule_id="seeds")
        self.now = datetime.now(UTC)

    def tearDown(self):
        with self.base_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))

    def collection(self):
        identity = uuid4()
        self.store.create_collection(identity, CollectionSpec(page_limit=1))
        return identity

    def test_publication_and_retry_reject_claims_expired_during_row_lock_wait(self):
        from datetime import timedelta
        from threading import Event
        import time
        from sqlalchemy import func
        from periplus.crawl.runtime.frontier_models import FrontierOutboxRecord
        for operation in ('publish', 'retry'):
            with self.subTest(operation=operation):
                self.collection()
                delivery = self.store.claim_outbox(batch=1)[0]
                entered = Event()
                def finish():
                    entered.set()
                    if operation == 'publish':
                        return self.store.mark_outbox_published(delivery)
                    return self.store.release_outbox(delivery, 'delivery failed')
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with self.sessions.begin() as locker:
                        row = locker.get(FrontierOutboxRecord, delivery.message_id, with_for_update=True)
                        row.claim_expires_at = locker.scalar(select(func.clock_timestamp())) + timedelta(milliseconds=200)
                        locker.flush()
                        result = pool.submit(finish)
                        self.assertTrue(entered.wait(5))
                        time.sleep(0.3)
                    self.assertFalse(result.result(timeout=5))
                with self.sessions() as session:
                    row = session.get(FrontierOutboxRecord, delivery.message_id)
                    self.assertIsNone(row.published_at)
                    self.assertIsNone(row.last_error)
                    self.assertEqual(row.claim_token, delivery.claim_token)

    def test_default_delivery_claims_and_release_use_database_clock(self):
        from unittest.mock import patch
        self.collection()
        with patch('periplus.crawl.runtime.frontier_store.datetime') as clock:
            clock.now.side_effect = AssertionError('process clock must not own delivery timing')
            self.assertIsNotNone(self.store.claim_collection())
            delivery = self.store.claim_outbox(batch=1)[0]
            self.assertTrue(self.store.mark_outbox_published(delivery))
            receipts = self.store.claim_ingestion_receipts()
            self.assertEqual(len(receipts), 1)
            self.assertTrue(self.store.defer_ingestion_receipt(receipts[0], 'pending'))
            self.assertEqual(self.store.expired_dispatches(), ())

    def test_collection_release_cannot_accept_a_claim_expired_during_row_lock_wait(self):
        from datetime import timedelta
        from threading import Event
        import time
        from sqlalchemy import func
        from periplus.crawl.control.collections.models import CollectionRecord
        self.collection()
        work = self.store.claim_collection()
        entered = Event()
        def release():
            entered.set()
            return self.store.release_collection(work)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.sessions.begin() as locker:
                collection = locker.get(CollectionRecord, work.collection_id, with_for_update=True)
                collection.service_expires_at = locker.scalar(select(func.clock_timestamp())) + timedelta(milliseconds=200)
                locker.flush()
                result = pool.submit(release)
                self.assertTrue(entered.wait(5))
                time.sleep(0.3)
            self.assertFalse(result.result(timeout=5))
        self.assertEqual(self.store.get_collection(work.collection_id).service_token, work.token)

    def test_control_lock_wait_cannot_extend_an_expired_capture_authorization(self):
        from datetime import timedelta
        from threading import Event
        import time
        from unittest.mock import patch
        from sqlalchemy import func
        identity = self.collection()
        admission = self.store.admit(identity, 'https://clock.example/', self.context, self.policy)
        work = self.store.dispatch(admission.acquisition_id)
        entered = Event()
        original = self.store._control
        def waiting(session):
            entered.set()
            return original(session)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.sessions.begin() as locker:
                locker.scalar(select(FrontierControlRecord).where(FrontierControlRecord.id == 1).with_for_update())
                now = locker.scalar(select(func.clock_timestamp()))
                locker.get(AcquisitionRecord, admission.acquisition_id).claim_expires_at = now + timedelta(milliseconds=200)
                locker.flush()
                with patch.object(self.store, '_control', side_effect=waiting):
                    result = pool.submit(self.store.begin_attempt, work.acquisition_id, work.generation)
                    self.assertTrue(entered.wait(5))
                    time.sleep(0.3)
            self.assertFalse(result.result(timeout=5))
        self.assertEqual(self.store.get_acquisition(admission.acquisition_id).attempt_count, 0)

    def test_new_dispatch_lease_begins_after_its_control_lock_wait(self):
        from datetime import timedelta
        from threading import Event
        import time
        from unittest.mock import patch
        from sqlalchemy import func
        identity = self.collection()
        admission = self.store.admit(identity, 'https://lease.example/', self.context, self.policy)
        entered = Event()
        original = self.store._control
        def waiting(session):
            entered.set()
            return original(session)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.sessions.begin() as locker:
                locker.scalar(select(FrontierControlRecord).where(FrontierControlRecord.id == 1).with_for_update())
                with patch.object(self.store, '_control', side_effect=waiting):
                    result = pool.submit(self.store.dispatch, admission.acquisition_id, lease_seconds=1)
                    self.assertTrue(entered.wait(5))
                    time.sleep(0.3)
                    released_after = locker.scalar(select(func.clock_timestamp()))
            self.assertIsNotNone(result.result(timeout=5))
        acquisition = self.store.get_acquisition(admission.acquisition_id)
        self.assertGreaterEqual(acquisition.claim_expires_at, released_after + timedelta(seconds=1))

    def test_destination_rejection_and_physical_start_have_one_fenced_winner(self):
        for index in range(4):
            identity = self.collection()
            admission = self.store.admit(identity, f'https://destination{index}.example/',
                                         self.context, self.policy, now=self.now)
            work = self.store.dispatch(admission.acquisition_id, now=self.now)
            gate = Barrier(2)
            def start():
                gate.wait(timeout=10)
                return self.store.begin_attempt(work.acquisition_id, work.generation, now=self.now)
            def reject():
                gate.wait(timeout=10)
                return self.store.reject_destination(work.acquisition_id, work.generation)
            with ThreadPoolExecutor(max_workers=2) as pool:
                starting = pool.submit(start)
                rejected = pool.submit(reject).result()
                started = starting.result()
            self.assertNotEqual(started, rejected)
            acquisition = self.store.get_acquisition(work.acquisition_id)
            self.assertEqual(acquisition.attempt_count, int(started))
            self.assertEqual(acquisition.status, 'dispatched' if started else 'cancelled')
            self.assertEqual(acquisition.terminal_reason, None if started else 'non_public_destination')
            self.assertEqual(acquisition.attempt_reserved_ms, 125000 if started else 0)

    def test_domain_deferral_racing_start_has_one_physical_accounting_winner(self):
        for index in range(8):
            identity = self.collection()
            admission = self.store.admit(identity, f"https://race{index}.example/", self.context,
                                         self.policy, now=self.now)
            work = self.store.dispatch(admission.acquisition_id, now=self.now)
            gate = Barrier(2)
            def start():
                gate.wait(timeout=10)
                return self.store.begin_attempt(work.acquisition_id, work.generation, now=self.now)
            def defer():
                gate.wait(timeout=10)
                return self.store.defer_unstarted(work.acquisition_id, work.generation,
                                                  delay_seconds=60, now=self.now)
            with ThreadPoolExecutor(max_workers=2) as pool:
                starting = pool.submit(start)
                deferred = pool.submit(defer).result()
                started = starting.result()
            self.assertNotEqual(started, deferred)
            acquisition = self.store.get_acquisition(work.acquisition_id)
            self.assertEqual(acquisition.attempt_count, int(started))
            self.assertEqual(acquisition.attempt_reserved_ms, 125000 if started else 0)
            self.assertEqual(acquisition.status, "dispatched" if started else "retry")
            self.assertEqual(self.store.get_collection(identity).consumed, 1)

    def test_current_domain_policy_filters_and_fences_final_start(self):
        from periplus.crawl.control.domain_policies.models import DomainPolicy
        from periplus.crawl.control.domain_policies.schemas import DomainPolicyCreateRequest
        from periplus.crawl.control.domain_policies.service import create_domain_policy, update_domain_policy
        with self.sessions.begin() as session:
            create_domain_policy(session, DomainPolicyCreateRequest(slug="wild", host_match="*.example.com", paused=True))
            policy_id = create_domain_policy(session, DomainPolicyCreateRequest(slug="api", host_match="api.example.com")).id
        blocked = self.store.admit(self.collection(), "https://shop.example.com/", self.context, self.policy, now=self.now)
        allowed = self.store.admit(self.collection(), "https://api.example.com/", self.context, self.policy, now=self.now)
        work = self.store.dispatch_next(now=self.now)
        self.assertEqual(work.acquisition_id, allowed.acquisition_id)
        self.assertIsNone(self.store.dispatch(blocked.acquisition_id, now=self.now))
        expected = self.store.current_domain_policy(work.acquisition_id)
        gate = Barrier(2)
        def start():
            gate.wait(timeout=10)
            return self.store.begin_attempt(work.acquisition_id, work.generation, domain_policy=expected, now=self.now)
        def pause():
            gate.wait(timeout=10)
            with self.sessions.begin() as session:
                return update_domain_policy(session, policy=session.get(DomainPolicy, policy_id),
                                            expected_version=1, paused=True).version
        with ThreadPoolExecutor(max_workers=2) as pool:
            starting = pool.submit(start)
            version = pool.submit(pause).result()
            started = starting.result()
        self.assertEqual(version, 2)
        self.assertTrue(self.store.current_domain_policy(work.acquisition_id).paused)
        acquisition = self.store.get_acquisition(work.acquisition_id)
        self.assertEqual(acquisition.attempt_count, int(started))
        if started:
            self.assertEqual(acquisition.attempt_domain_policy['version'], 1)
            self.assertFalse(acquisition.attempt_domain_policy['paused'])
        else:
            self.assertIsNone(acquisition.attempt_domain_policy)

    def test_concurrent_interests_share_one_acquisition_and_reserve_once(self):
        identities = [self.collection() for _ in range(4)]
        gate = Barrier(4)
        def admit(identity):
            gate.wait(timeout=10)
            return self.store.admit(identity, "https://example.com/", self.context,
                                    self.policy, now=self.now)
        with ThreadPoolExecutor(max_workers=4) as pool:
            admitted = list(pool.map(admit, identities))
        self.assertEqual(len({a.acquisition_id for a in admitted}), 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            repeated = list(pool.map(admit, [identities[0]] * 4))
        self.assertTrue(all(not a.created for a in repeated))
        with self.sessions() as session:
            self.assertEqual(len(list(session.scalars(select(AcquisitionRecord)))), 1)
            self.assertEqual(len(list(session.scalars(select(InterestRecord)))), 4)
        self.assertEqual(self.store.get_collection(identities[0]).reserved, 1)

    def test_cancellation_dispatch_race_has_one_accounting_winner(self):
        # Exercise both competing transitions repeatedly with fresh identities.
        for index in range(12):
            identity = self.collection()
            admission = self.store.admit(identity, f"https://site{index}.example/", self.context,
                                         self.policy, now=self.now)
            gate = Barrier(2)
            def cancel():
                gate.wait(timeout=10)
                self.store.stop_collection(identity, now=self.now)
            def dispatch():
                gate.wait(timeout=10)
                return self.store.dispatch(admission.acquisition_id, now=self.now)
            with ThreadPoolExecutor(max_workers=2) as pool:
                cancellation = pool.submit(cancel)
                work = pool.submit(dispatch).result()
                cancellation.result()
            record = self.store.get_collection(identity)
            self.assertEqual(record.reserved, 0)
            self.assertEqual(record.consumed, int(work is not None))
            interest = self.store.get_interest(admission.interest_id)
            self.assertEqual(interest.budget_state, "consumed" if work else "released")

    def test_parallel_publishers_claim_disjoint_batches(self):
        for index in range(12):
            admission = self.store.admit(self.collection(), f"https://site{index}.example/",
                                         self.context, self.policy, now=self.now)
            self.store.dispatch(admission.acquisition_id, now=self.now)
        gate = Barrier(4)
        def claim(_):
            gate.wait(timeout=10)
            return self.store.claim_outbox(batch=9, now=self.now)
        with ThreadPoolExecutor(max_workers=4) as pool:
            batches = list(pool.map(claim, range(4)))
        messages = [delivery.message_id for batch in batches for delivery in batch]
        self.assertEqual(len(messages), 36)
        self.assertEqual(len(set(messages)), 36)
        self.assertEqual(self.store.claim_outbox(now=self.now), [])

    def test_puback_then_marker_commit_failure_replays_without_second_attempt(self):
        import asyncio
        from datetime import timedelta
        from unittest.mock import AsyncMock, patch
        from sqlalchemy import event
        from periplus.crawl.runtime.frontier_models import FrontierOutboxRecord
        from periplus.crawl.runtime.frontier_outbox import publish_outbox_once

        admission = self.store.admit(self.collection(), 'https://publication.example/',
                                     self.context, self.policy, now=self.now)
        work = self.store.dispatch(admission.acquisition_id, now=self.now)
        deliveries = self.store.claim_outbox(now=self.now)
        capture, = [delivery for delivery in deliveries if delivery.kind == 'capture']
        for delivery in deliveries:
            if delivery.kind != 'capture':
                self.store.mark_outbox_published(delivery, now=self.now)
        jetstream = AsyncMock()
        ingestion = AsyncMock()
        mark = self.store.mark_outbox_published

        def fail_commit(session):
            # Send the UPDATE to Postgres, then fail before COMMIT. Closing the
            # transaction must roll it back even though publication succeeded.
            session.flush()
            raise RuntimeError('injected publication marker commit failure')

        def failing_mark(delivery):
            event.listen(self.sessions.class_, 'before_commit', fail_commit)
            try:
                return mark(delivery, now=self.now)
            finally:
                event.remove(self.sessions.class_, 'before_commit', fail_commit)

        with patch.object(self.store, 'claim_outbox', return_value=[capture]), \
                patch.object(self.store, 'mark_outbox_published', side_effect=failing_mark):
            with self.assertRaisesRegex(RuntimeError, 'marker commit failure'):
                asyncio.run(publish_outbox_once(self.store, jetstream, ingestion))
        jetstream.publish.assert_awaited_once()
        with self.sessions() as session:
            row = session.get(FrontierOutboxRecord, capture.message_id)
            self.assertIsNone(row.published_at)
            self.assertEqual(row.claim_token, capture.claim_token)

        self.assertTrue(self.store.begin_attempt(work.acquisition_id, work.generation, now=self.now))
        self.assertEqual(self.store.claim_outbox(now=self.now), [])
        later = self.now + timedelta(seconds=61)
        replay, = self.store.claim_outbox(now=later)
        self.assertEqual(replay.message_id, capture.message_id)
        self.assertEqual(replay.payload, capture.payload)
        self.assertNotEqual(replay.claim_token, capture.claim_token)
        with patch.object(self.store, 'claim_outbox', return_value=[replay]), \
                patch.object(self.store, 'mark_outbox_published',
                             side_effect=lambda delivery: mark(delivery, now=later)):
            asyncio.run(publish_outbox_once(self.store, jetstream, ingestion))
        self.assertEqual(jetstream.publish.await_args_list[0], jetstream.publish.await_args_list[1])
        self.assertFalse(self.store.begin_attempt(work.acquisition_id, work.generation, now=later))
        with self.sessions() as session:
            self.assertIsNotNone(session.get(FrontierOutboxRecord, capture.message_id).published_at)
            self.assertEqual(session.get(AcquisitionRecord, work.acquisition_id).attempt_count, 1)
        self.assertEqual(self.store.claim_outbox(now=later), [])
        ingestion.enqueue.assert_not_awaited()

    def test_parallel_schedulers_dispatch_distinct_work_with_domain_bounds(self):
        for index in range(8):
            self.store.admit(self.collection(), f"https://site{index}.example/", self.context,
                             self.policy, now=self.now)
        gate = Barrier(4)
        def dispatch(_):
            gate.wait(timeout=10)
            return self.store.dispatch_next(now=self.now)
        with ThreadPoolExecutor(max_workers=4) as pool:
            work = list(pool.map(dispatch, range(4)))
        self.assertEqual(len({item.acquisition_id for item in work}), 4)
        with self.sessions() as session:
            control = session.get(FrontierControlRecord, 1)
            self.assertEqual((control.pending_count, control.active_count), (4, 4))

    def test_parallel_selection_workers_claim_different_collections(self):
        identities = {self.collection() for _ in range(4)}
        gate = Barrier(4)
        def claim(_):
            gate.wait(timeout=10)
            return self.store.claim_collection()
        with ThreadPoolExecutor(max_workers=4) as pool:
            work = list(pool.map(claim, range(4)))
        self.assertEqual({item.collection_id for item in work}, identities)
        self.assertIsNone(self.store.claim_collection())

    def test_selection_priority_prefers_due_work_but_cannot_erase_age_or_backoff(self):
        from datetime import timedelta
        from periplus.crawl.control.collections.models import CollectionRecord
        low, high, deferred = self.collection(), self.collection(), self.collection()
        with self.sessions.begin() as session:
            for identity in (low, high, deferred):
                record = session.get(CollectionRecord, identity)
                record.service_after = self.now
                record.priority = -10 if identity == low else 10
            session.get(CollectionRecord, deferred).service_after = self.now + timedelta(seconds=5)
        first = self.store.claim_collection(now=self.now)
        self.assertEqual(first.collection_id, high)
        self.assertTrue(self.store.release_collection(first, now=self.now + timedelta(seconds=21)))
        second = self.store.claim_collection(now=self.now + timedelta(seconds=21))
        self.assertEqual(second.collection_id, deferred)
        self.assertTrue(self.store.release_collection(second, now=self.now + timedelta(seconds=21)))
        # Both higher-priority requests have now received service. Their refreshed
        # timestamps cannot keep overtaking the older low-priority request.
        third = self.store.claim_collection(now=self.now + timedelta(seconds=21))
        self.assertEqual(third.collection_id, low)

    def test_concurrent_admin_collection_admission_remains_open(self):
        gate = Barrier(4)
        def create(_):
            gate.wait(timeout=10)
            return self.collection()
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(create, range(4)))
        self.assertEqual(sum(identity is not None for identity in outcomes), 4)

    def test_concurrent_control_updates_accept_only_one_policy_version(self):
        from periplus.crawl.control.collections.frontier_controls import (
            ControlVersionConflict, ReplaceFrontierSettings,
        )
        initial = self.store.control_view()
        gate = Barrier(2)
        def change(limit):
            gate.wait(timeout=10)
            try:
                return self.store.replace_controls(ReplaceFrontierSettings(
                    expected_version=initial.policy_version,
                    settings=initial.settings.model_copy(update={"dispatch_limit": limit}),
                ), actor=f"operator-{limit}")
            except ControlVersionConflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, (2, 3)))
        winner, = [result for result in results if result is not None]
        current = self.store.control_view()
        self.assertEqual(current.policy_version, initial.policy_version + 1)
        self.assertEqual(current.settings, winner.settings)
        self.assertEqual(current.updated_by, winner.updated_by)

    def test_concurrent_dispatch_cannot_exceed_active_capacity(self):
        from periplus.crawl.control.collections.frontier_controls import ReplaceFrontierSettings
        initial = self.store.control_view()
        self.store.replace_controls(ReplaceFrontierSettings(
            expected_version=initial.policy_version,
            settings=initial.settings.model_copy(update={"dispatch_limit": 1}),
        ), actor="test", now=self.now)
        candidates = [self.store.admit(self.collection(), f"https://example.com/{index}",
                                      self.context, self.policy, now=self.now) for index in range(2)]
        gate = Barrier(2)
        def dispatch(candidate):
            gate.wait(timeout=10)
            return self.store.dispatch(candidate.acquisition_id, now=self.now)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(dispatch, candidates))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.store.control_view().dispatched_acquisitions, 1)
        with self.sessions() as session:
            interests = list(session.scalars(select(InterestRecord)))
        self.assertEqual(sorted(interest.budget_state for interest in interests), ["consumed", "reserved"])

    def test_receipt_workers_claim_disjoint_published_evidence(self):
        for _ in range(6):
            self.collection()
        for delivery in self.store.claim_outbox(now=self.now):
            self.store.mark_outbox_published(delivery, now=self.now)
        gate = Barrier(2)
        def claim(_):
            gate.wait(timeout=10)
            return self.store.claim_ingestion_receipts(batch=4, now=self.now)
        with ThreadPoolExecutor(max_workers=2) as pool:
            batches = list(pool.map(claim, range(2)))
        messages = [delivery.message_id for batch in batches for delivery in batch]
        self.assertEqual(len(messages), 6)
        self.assertEqual(len(set(messages)), 6)
        self.assertEqual(self.store.claim_ingestion_receipts(now=self.now), [])





    def test_exclusion_change_and_capture_start_serialize_without_cancelling_started_work(self):
        from periplus.crawl.control.collections.frontier_controls import ReplaceFrontierSettings
        own = self.store.admit(self.collection(), "https://example.com/", self.context, self.policy, now=self.now)
        work = self.store.dispatch(own.acquisition_id, now=self.now)
        current = self.store.control_view()
        settings = current.settings.model_dump(mode="json") | {"exclusions": [{"host": "example.com"}]}
        gate = Barrier(2)
        def start():
            gate.wait(timeout=10)
            return self.store.begin_attempt(own.acquisition_id, work.generation, now=self.now)
        def exclude():
            gate.wait(timeout=10)
            self.store.replace_controls(ReplaceFrontierSettings(expected_version=current.policy_version, settings=settings), actor="test")
            self.store.reconcile_exclusions()
        with ThreadPoolExecutor(max_workers=2) as pool:
            beginning = pool.submit(start)
            excluding = pool.submit(exclude)
            started = beginning.result()
            excluding.result()
        acquisition = self.store.get_acquisition(own.acquisition_id)
        view = self.store.control_view()
        self.assertEqual(acquisition.status, "dispatched" if started else "cancelled")
        self.assertEqual(view.dispatched_acquisitions, int(started))

    def test_long_url_admission_keeps_full_identity_and_deduplicates_concurrent_contexts(self):
        from hashlib import sha256
        token = "".join(sha256(str(i).encode()).hexdigest() for i in range(100))
        url = "https://example.com/path?signature=" + token
        identity = uuid4()
        self.store.create_collection(identity, CollectionSpec(page_limit=2))
        gate = Barrier(2)
        def admit(depth):
            gate.wait(timeout=10)
            return self.store.admit(identity, url + "#fragment", SelectionContext(depth=0, rule_id=f"rule-{depth}"), self.policy)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(admit, range(2)))
        self.assertEqual(results[0].interest_id, results[1].interest_id)
        self.assertEqual(sum(result.created for result in results), 1)
        retained = self.store.get_interest(results[0].interest_id)
        self.assertEqual(retained.url, url)
        other = self.store.admit(identity, url + "0", self.context, self.policy)
        self.assertNotEqual(other.interest_id, retained.id)

    def test_navigation_retirement_serializes_with_branch_reuse(self):
        from datetime import timedelta
        from frontier_fixtures import navigation_package
        from periplus.platform.catalogue.records import VisitEvidence, VisitRecord
        parent = self.store.admit(self.collection(), "https://example.com/", self.context, self.policy, now=self.now)
        package = navigation_package().model_copy(update={"object_name": f"runtime/navigation/{parent.acquisition_id.hex}/{'a' * 64}.arrow"})
        with self.sessions.begin() as session:
            acquisition = session.get(AcquisitionRecord, parent.acquisition_id)
            acquisition.status = "succeeded"
            acquisition.navigation = package.model_dump(mode="json")
            acquisition.completed_at = self.now
            acquisition.evidence_snapshot = 7
            acquisition.pending_key = None
            acquisition.outcome = VisitEvidence(visit=VisitRecord(capture_policy=capture_policy(), visit_id=acquisition.id,
                requested_url=acquisition.url, admitted_at=self.now, started_at=self.now,
                finished_at=self.now, outcome="succeeded"), attempts=()).model_dump(mode="json")
            session.get(InterestRecord, parent.interest_id).status = "settled"
            session.get(FrontierControlRecord, 1).pending_count = 0
        request = uuid4()
        self.store.create_collection(request, CollectionSpec(max_depth=1))
        gate = Barrier(2)
        def retire():
            gate.wait(timeout=10)
            return self.store.retire_navigation(parent.acquisition_id, package.object_name,
                modified_at=self.now, cutoff=self.now + timedelta(seconds=1), orphan_cutoff=self.now)
        def reuse():
            gate.wait(timeout=10)
            return self.store.admit(request, "https://example.com/", self.context, self.policy, now=self.now)
        with ThreadPoolExecutor(max_workers=2) as pool:
            retiring = pool.submit(retire)
            admitting = pool.submit(reuse)
            retired, admission = retiring.result(), admitting.result()
        self.assertEqual(retired, admission.mode != "reused")
        if not retired:
            self.assertEqual(admission.acquisition_id, parent.acquisition_id)
            self.assertIsNotNone(self.store.get_acquisition(parent.acquisition_id).navigation)

    def test_concurrent_acquisition_admission_remains_open(self):
        identities = [self.collection(), self.collection()]
        gate = Barrier(2)
        def admit(index):
            gate.wait(timeout=10)
            return self.store.admit(identities[index], f"https://host-{index}.example/", self.context, self.policy)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(admit, range(2)))
        self.assertEqual(sum(result is not None for result in results), 2)
        self.assertEqual(self.store.control_view().retained_acquisitions, 2)

    def test_concurrent_cleanup_deletes_one_unreferenced_acquisition_once(self):
        from datetime import timedelta
        identity = uuid4()
        with self.sessions.begin() as session:
            session.add(AcquisitionRecord(id=identity, url="https://retired.example/", domain="retired.example",
                capture_key=str(identity), requirements=self.policy.model_dump(mode="json"),
                 status="cancelled", completed_at=self.now))
        gate = Barrier(2)
        def cleanup(_):
            gate.wait(timeout=10)
            return self.store.cleanup_acquisitions(cutoff=self.now + timedelta(seconds=1)).removed
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(cleanup, range(2)))
        self.assertEqual(sorted(results), [0, 1])
        self.assertEqual(self.store.control_view().retained_acquisitions, 0)

    def test_concurrent_collection_cleanup_requires_durable_handoff_and_deletes_once(self):
        from datetime import timedelta
        from periplus.crawl.runtime.frontier_models import FrontierOutboxRecord
        identity = self.collection()
        self.store.stop_collection(identity, now=self.now)
        with self.sessions.begin() as session:
            for row in session.scalars(select(FrontierOutboxRecord).where(FrontierOutboxRecord.collection_id == identity)):
                row.committed_snapshot = 7
        gate = Barrier(2)
        def cleanup(_):
            gate.wait(timeout=10)
            return self.store.cleanup_collections(cutoff=self.now + timedelta(seconds=1)).removed
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(cleanup, range(2)))
        self.assertEqual(sorted(results), [0, 1])
        self.assertIsNone(self.store.get_collection(identity))

    def test_concurrent_schedule_ticks_create_one_request_and_outbox(self):
        from datetime import timedelta
        from periplus.crawl.control.schedules.models import RequestDefinitionRecord, ScheduleRecord
        from periplus.crawl.control.schedules.schemas import DefinitionInput, ScheduleInput
        from periplus.crawl.control.schedules.service import ScheduleStore
        from periplus.crawl.runtime.request_schedules import create_due_requests
        from periplus.crawl.runtime.frontier_models import FrontierOutboxRecord
        for model in (RequestDefinitionRecord, ScheduleRecord):
            model.__table__.create(self.engine)
        schedules = ScheduleStore(self.sessions)
        definition = schedules.save_definition(DefinitionInput(name='Race', specification=CollectionSpec(seed_urls=('https://example.com/',))))
        due = self.now + timedelta(minutes=1)
        schedule = schedules.save_schedule(definition.id, ScheduleInput(kind='interval', interval_seconds=60, start_at=due, max_count=1))
        gate = Barrier(2)
        def tick(_):
            gate.wait(timeout=10)
            return create_due_requests(schedules, due)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(tick, range(2)))
        self.assertEqual(sum(map(len, results)), 1)
        self.assertEqual(schedules.schedules()[0].execution_count, 1)
        with self.sessions() as session:
            rows = list(session.scalars(select(FrontierOutboxRecord)))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].payload['specification']['origin']['schedule_id'], str(schedule.id))
