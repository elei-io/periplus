"""Public current activity and committed velocity are separate, bounded measurements."""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from sqlalchemy import select
from schema_fixture import create_engine
from sqlalchemy.orm import sessionmaker

from frontier_fixtures import policy_snapshot
from test_frontier_store import TABLES
from periplus.crawl.control.collections.schemas import CollectionSpec, SelectionContext
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from periplus.crawl.control.domain_policies.service import ensure_default_domain_policy
from periplus.crawl.runtime.frontier_models import AcquisitionRecord, FrontierControlRecord, InterestRecord
from periplus.crawl.runtime.frontier_store import FrontierStore
from periplus.crawl.runtime.live import current_activity


class CurrentLiveTests(unittest.TestCase):
    def test_current_counts_and_domain_preview_include_all_classes_before_limits(self):
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        for table in TABLES:
            table.create(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        with sessions.begin() as session:
            session.add(FrontierControlRecord(id=1))
            ensure_default_domain_policy(session)
        store = FrontierStore(sessions)
        policy = EffectivePolicySnapshot.model_validate(policy_snapshot())
        for index in range(13):
            identity = uuid4()
            store.create_collection(identity, CollectionSpec(request_class='admin' if index == 12 else 'public'))
            store.admit(identity, f'https://domain{index}.example/', SelectionContext(depth=0, rule_id='seed'), policy)
        view = current_activity(sessions)
        self.assertEqual(view.queued, 13)
        self.assertEqual(view.dispatched, 0)
        self.assertEqual(len(view.domains), 10)
        self.assertTrue(view.more_domains)
        self.assertEqual(len(view.upcoming), 5)
        self.assertIsNone(view.next_start_estimate)
        self.assertIsNotNone(view.oldest_wait_at.utcoffset())
        with sessions.begin() as session:
            records = list(session.scalars(select(AcquisitionRecord).order_by(AcquisitionRecord.id).limit(2)))
            records[1].url = records[0].url
            records[1].domain = records[0].domain
            repeated_domain = records[0].domain
        counted = current_activity(sessions)
        domain = next(item for item in counted.domains if item.domain == repeated_domain)
        self.assertEqual(domain.queued, 2)
        self.assertEqual(domain.unique_queued_urls, 1)
        identity = uuid4()
        store.create_collection(identity, CollectionSpec(request_class='public'))
        store.admit(identity, 'https://extra.example/', SelectionContext(depth=0, rule_id='seed'), policy)
        with sessions.begin() as session:
            for index, acquisition in enumerate(session.scalars(select(AcquisitionRecord))):
                acquisition.status = 'dispatched'
                acquisition.attempt_started_at = datetime.now(UTC) if index % 2 else None
        view = current_activity(sessions)
        self.assertEqual(view.dispatched, 14)
        self.assertEqual(len(view.active), 12)
        self.assertTrue(view.more_active)
        self.assertTrue(any(item.attempt_started_at is None for item in view.active))
        self.assertTrue(any(item.attempt_started_at is not None for item in view.active))
        self.assertTrue(all(item.attempt_started_at.utcoffset() is not None
                            for item in view.active if item.attempt_started_at is not None))

    def test_request_domain_counts_cover_all_waiting_urls_without_private_associations(self):
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        for table in TABLES:
            table.create(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        with sessions.begin() as session:
            session.add(FrontierControlRecord(id=1))
            ensure_default_domain_policy(session)
        store = FrontierStore(sessions)
        policy = EffectivePolicySnapshot.model_validate(policy_snapshot())
        public, private, other = uuid4(), uuid4(), uuid4()
        for identity in (public, private, other):
            store.create_collection(identity, CollectionSpec(request_class='admin' if identity == private else 'public', page_limit=100))
        for index in range(60):
            store.admit(public, f'https://example.com/{index}', SelectionContext(depth=0, rule_id='seed'), policy)
        store.admit(other, 'https://example.com/other', SelectionContext(depth=0, rule_id='seed'), policy)
        store.admit(private, 'https://example.com/private', SelectionContext(depth=0, rule_id='seed'), policy)
        view = current_activity(sessions, collection_id=public)
        self.assertEqual(len(view.upcoming), 5)
        self.assertEqual(view.domains[0].unique_queued_urls, 62)
        self.assertEqual(view.domains[0].request_queued_urls, 60)
        with sessions.begin() as session:
            private_interest = session.scalar(select(InterestRecord).where(InterestRecord.collection_id == private))
            public_interest = session.scalar(select(InterestRecord).where(InterestRecord.collection_id == public))
            private_interest.acquisition_id = session.scalar(select(InterestRecord.acquisition_id).where(InterestRecord.collection_id == other))
            session.get(AcquisitionRecord, public_interest.acquisition_id).status = 'dispatched'
        view = current_activity(sessions, collection_id=public)
        self.assertEqual(view.domains[0].request_queued_urls, 59)
        self.assertEqual(view.domains[0].unique_queued_urls, 61)
        self.assertEqual(current_activity(sessions, collection_id=private).domains[0].request_queued_urls, 1)
        self.assertIsNone(current_activity(sessions).domains[0].request_queued_urls)
