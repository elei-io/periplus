"""Background continuation uses retained public navigation and durable admission."""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from unittest.mock import Mock
import unittest
from uuid import uuid4

from periplus.crawl.control.domain_policies.service import ensure_default_domain_policy

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from frontier_fixtures import navigation_package, policy_snapshot
from test_frontier_store import TABLES
from test_selection_sql import navigation_bytes
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from periplus.crawl.runtime.background_seen import BackgroundLookupUnavailable
from periplus.crawl.runtime.background_selection import background_candidates, service_background
from periplus.crawl.runtime.frontier_models import AcquisitionRecord, FrontierControlRecord
from periplus.crawl.runtime.frontier_store import FrontierStore
from periplus.crawl.runtime.selection_contract import SelectionCheckpoint


class BackgroundSelectionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        for table in TABLES:
            table.create(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.store = FrontierStore(self.sessions)
        self.policy = EffectivePolicySnapshot.model_validate(policy_snapshot())
        with self.sessions.begin() as session:
            session.add(FrontierControlRecord(id=1, background_share=10, captures_per_minute=0))
            ensure_default_domain_policy(session)

    def parent(self, urls, *, visibility="public", navigation=True):
        payload = navigation_bytes(urls)
        package = navigation_package().model_copy(update={"byte_size": len(payload), "sha256": sha256(payload).hexdigest()})
        identity = uuid4()
        with self.sessions.begin() as session:
            session.add(AcquisitionRecord(id=identity, url="https://parent.example/", domain="parent.example",
                capture_key=str(identity), requirements=self.policy.model_dump(mode="json"),
                visibility=visibility, access_context=visibility, status="succeeded",
                background_after=datetime.now(UTC) - timedelta(seconds=1),
                navigation=package.model_dump(mode="json") if navigation else None))
        objects = Mock()
        objects.size.return_value = len(payload)
        objects.open.side_effect = lambda name: BytesIO(payload)
        return identity, objects

    def test_candidates_bound_hosts_traps_count_and_bytes(self):
        urls = [f"https://host{host:02}.example/{page:02}" for host in range(30) for page in range(20)]
        selected = background_candidates(navigation_bytes(urls + ["https://host00.example/calendar/2026"]))
        self.assertEqual(len(selected.urls), 64)
        self.assertEqual(len({url.split('/')[2] for url in selected.urls[:30]}), 30)
        self.assertFalse(any("calendar" in url for url in selected.urls))
        self.assertIsNone(background_candidates(navigation_bytes(["https://host.example/?session=x"])))

    def test_claim_excludes_private_missing_navigation_and_disabled_exploration(self):
        self.parent([], visibility="private")
        self.parent([], navigation=False)
        self.assertIsNone(self.store.claim_background())
        identity, _ = self.parent([])
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).background_share = 0
        self.assertIsNone(self.store.claim_background())
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).background_share = 10
        work = self.store.claim_background()
        self.assertEqual(work.acquisition_id, identity)
        self.assertIsNone(self.store.claim_background())

    def test_continuation_resumes_capacity_without_another_query_and_dispatches_independently(self):
        identity, objects = self.parent(["https://one.example/", "https://two.example/"])
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).admission_limit = 1
        work = self.store.claim_background()
        query = Mock(return_value=SelectionCheckpoint(urls=(), source_snapshot="7", source_query_id="q"))
        self.assertFalse(service_background(self.store, work, lambda url: self.policy, objects, query))
        self.assertFalse(self.store.get_acquisition(identity).background_selected)
        self.assertIsNotNone(self.store.dispatch_next())
        self.assertTrue(service_background(self.store, work, lambda url: self.policy, objects, query))
        query.assert_called_once()
        objects.open.assert_called_once()
        self.assertTrue(self.store.get_acquisition(identity).background_selected)
        self.assertTrue(self.store.release_background(work, complete=True))
        self.assertIsNone(self.store.claim_background())

    def test_lookup_failure_preserves_check_and_empty_navigation_finishes_without_query(self):
        identity, objects = self.parent(["https://one.example/"])
        work = self.store.claim_background()
        query = Mock(side_effect=RuntimeError("lake unavailable"))
        with self.assertRaises(BackgroundLookupUnavailable):
            service_background(self.store, work, lambda url: self.policy, objects, query)
        self.assertEqual(self.store.control_view().pending_acquisitions, 0)
        self.assertIsNotNone(self.store.resume_background_check(work))
        self.assertTrue(self.store.release_background(work, error="lake unavailable"))
        self.assertIsNone(self.store.claim_background())
        empty, empty_objects = self.parent([])
        empty_work = self.store.claim_background()
        self.assertEqual(empty_work.acquisition_id, empty)
        self.assertTrue(service_background(self.store, empty_work, lambda url: self.policy, empty_objects, query))
        self.store.release_background(empty_work, complete=True)
        self.assertTrue(self.store.get_acquisition(empty).background_selected)
        self.assertEqual(query.call_count, 1)

    def test_public_leaf_navigation_follows_background_enablement_private_never_seeds_it(self):
        public, _ = self.parent([])
        private, _ = self.parent([], visibility="private")
        self.assertTrue(self.store.needs_navigation(public))
        self.assertFalse(self.store.needs_navigation(private))
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).background_share = 0
        self.assertFalse(self.store.needs_navigation(public))

    def test_expired_service_claim_is_fenced_and_query_snapshot_is_refreshed_after_policy_change(self):
        identity, objects = self.parent(["https://one.example/", "https://two.example/"])
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).admission_limit = 1
        work = self.store.claim_background()
        query = Mock(return_value=SelectionCheckpoint(urls=(), source_snapshot="7", source_query_id="q"))
        self.assertFalse(service_background(self.store, work, lambda url: self.policy, objects, query))
        old_check = self.store.resume_background_check(work)
        with self.sessions.begin() as session:
            control = session.get(FrontierControlRecord, 1)
            control.policy_version += 1
            control.admission_limit = 2
            session.get(AcquisitionRecord, identity).background_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        replacement = self.store.claim_background()
        self.assertNotEqual(work.token, replacement.token)
        self.assertFalse(self.store.release_background(work, complete=True))
        self.assertTrue(service_background(self.store, replacement, lambda url: self.policy, objects, query))
        self.assertEqual(query.call_count, 2)
        self.assertNotEqual(self.store.resume_background_check(replacement).token, old_check.token)
