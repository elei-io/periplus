"""Public current activity and committed velocity are separate, bounded measurements."""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from frontier_fixtures import policy_snapshot
from test_frontier_store import TABLES
from periplus.crawl.control.collections.schemas import CollectionSpec, SelectionContext
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from periplus.crawl.control.domain_policies.service import ensure_default_domain_policy
from periplus.crawl.runtime.frontier_models import AcquisitionRecord, FrontierControlRecord, InterestRecord
from periplus.crawl.runtime.frontier_store import FrontierStore
from periplus.crawl.runtime.live import current_activity, read_live_history
from periplus.platform.catalogue.client import Catalogue
from periplus.platform.catalogue.config import CatalogueConfig


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


class HistoricalLiveTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.catalogue = Catalogue(CatalogueConfig('periplus', str(root/'metadata.duckdb'), str(root/'data'), 'ducklake'))
        self.addCleanup(self.catalogue.close)
        self.connection = self.catalogue.trusted_connection
        self.connection.execute('CREATE SCHEMA ingest')
        self.connection.execute('''CREATE TABLE ingest.visits (visit_id UUID, requested_url VARCHAR,
            finished_at TIMESTAMPTZ, visibility VARCHAR, outcome VARCHAR)''')
        self.connection.execute('CREATE TABLE ingest.attempts (attempt_id UUID, visit_id UUID, started_at TIMESTAMPTZ)')
        self.connection.execute('CREATE TABLE ingest.fulfillments (record_id UUID, requested_url VARCHAR, recorded_at TIMESTAMPTZ, visibility VARCHAR)')
        self.now = datetime.now(UTC)

    def visit(self, *, age=10, visibility='public', outcome='succeeded', domain='example.com', attempts=1):
        identity = uuid4()
        when = self.now - timedelta(seconds=age)
        self.connection.execute('INSERT INTO ingest.visits VALUES (?, ?, ?, ?, ?)',
            [identity, f'https://{domain}/', when, visibility, outcome])
        for _ in range(attempts):
            self.connection.execute('INSERT INTO ingest.attempts VALUES (?, ?, ?)', [uuid4(), identity, when])
        return identity

    def test_empty_committed_evidence_has_exact_zero_windows(self):
        value = read_live_history(self.catalogue, now=self.now)
        self.assertEqual(len(value.velocities), 2)
        self.assertTrue(all(row.domain is None and row.attempt_starts == 0 for row in value.velocities))
        self.assertEqual(value.recent, [])

    def test_attempts_captures_and_fulfillments_have_distinct_grains_and_public_windows(self):
        self.visit(attempts=2)
        self.visit(age=90, outcome='failed')
        self.visit(visibility='private', domain='private.example')
        self.visit(age=400, domain='old.example')
        self.visit(age=-30, domain='future.example')
        for _ in range(3):
            self.connection.execute('INSERT INTO ingest.fulfillments VALUES (?, ?, ?, ?)',
                [uuid4(), 'https://example.com/', self.now-timedelta(seconds=5), 'public'])
        self.connection.execute('INSERT INTO ingest.fulfillments VALUES (?, ?, ?, ?)',
            [uuid4(), 'https://private.example/', self.now, 'private'])
        value = read_live_history(self.catalogue, now=self.now)
        global_rows = {row.seconds: row for row in value.velocities if row.domain is None}
        self.assertEqual(global_rows[60].attempt_starts, 3)
        self.assertEqual(global_rows[300].attempt_starts, 4)
        self.assertEqual(global_rows[60].successful_captures, 2)
        self.assertEqual(global_rows[300].failed_captures, 1)
        self.assertEqual(global_rows[60].fulfillments, 4)
        self.assertEqual(global_rows[300].attempt_starts_per_minute, 0.8)
        self.assertIn('private.example', value.model_dump_json())
        self.assertNotIn('import.example', value.model_dump_json())
        self.assertNotIn('future.example', value.model_dump_json())
        self.assertEqual(len(value.recent), 3)
        self.assertTrue(all(item.query_ready is None for item in value.recent))

    def test_domain_and_recent_previews_are_bounded(self):
        for index in range(15):
            self.visit(domain=f'host{index}.example')
        value = read_live_history(self.catalogue, now=self.now)
        self.assertEqual(len(value.recent), 5)
        self.assertEqual(len(value.velocities), 22)
