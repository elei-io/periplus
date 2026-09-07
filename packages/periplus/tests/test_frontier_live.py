"""Public current activity and committed velocity are separate, bounded measurements."""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from frontier_fixtures import policy_snapshot
from test_frontier_store import TABLES
from periplus.crawl.control.collections.schemas import CollectionSpec, SelectionContext
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from periplus.crawl.control.domain_policies.service import ensure_default_domain_policy
from periplus.crawl.runtime.frontier_models import FrontierControlRecord
from periplus.crawl.runtime.frontier_store import FrontierStore
from periplus.crawl.runtime.live import current_activity, read_live_history
from periplus.platform.catalogue.client import Catalogue
from periplus.platform.catalogue.config import CatalogueConfig


class CurrentLiveTests(unittest.TestCase):
    def test_current_counts_and_domain_preview_exclude_private_work_before_limits(self):
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
            store.create_collection(identity, CollectionSpec(visibility='private' if index == 12 else 'public'))
            store.admit(identity, f'https://domain{index}.example/', SelectionContext(depth=0, rule_id='seed'), policy)
        view = current_activity(sessions)
        self.assertEqual(view.queued, 12)
        self.assertEqual(view.dispatched, 0)
        self.assertEqual(len(view.domains), 10)
        self.assertTrue(view.more_domains)
        self.assertEqual(len(view.upcoming), 5)
        self.assertNotIn('domain12', view.model_dump_json())
        self.assertIsNone(view.next_start_estimate)
        self.assertIsNotNone(view.oldest_wait_at.utcoffset())


class HistoricalLiveTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.catalogue = Catalogue(CatalogueConfig('periplus', str(root/'metadata.duckdb'), str(root/'data'), 'ducklake', ''))
        self.addCleanup(self.catalogue.close)
        self.connection = self.catalogue.trusted_connection
        self.connection.execute('CREATE SCHEMA ingest')
        self.connection.execute('''CREATE TABLE ingest.visits (visit_id UUID, requested_url VARCHAR,
            finished_at TIMESTAMPTZ, visibility VARCHAR, provenance STRUCT(kind VARCHAR), outcome VARCHAR)''')
        self.connection.execute('CREATE TABLE ingest.attempts (attempt_id UUID, visit_id UUID, started_at TIMESTAMPTZ)')
        self.connection.execute('CREATE TABLE ingest.fulfillments (record_id UUID, requested_url VARCHAR, recorded_at TIMESTAMPTZ, visibility VARCHAR)')
        self.now = datetime.now(UTC)

    def visit(self, *, age=10, visibility='public', kind='periplus', outcome='succeeded', domain='example.com', attempts=1):
        identity = uuid4()
        when = self.now - timedelta(seconds=age)
        self.connection.execute('INSERT INTO ingest.visits VALUES (?, ?, ?, ?, ?, ?)',
            [identity, f'https://{domain}/', when, visibility, {'kind':kind}, outcome])
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
        self.visit(kind='external', domain='import.example')
        self.visit(age=400, domain='old.example')
        self.visit(age=-30, domain='future.example')
        for _ in range(3):
            self.connection.execute('INSERT INTO ingest.fulfillments VALUES (?, ?, ?, ?)',
                [uuid4(), 'https://example.com/', self.now-timedelta(seconds=5), 'public'])
        self.connection.execute('INSERT INTO ingest.fulfillments VALUES (?, ?, ?, ?)',
            [uuid4(), 'https://private.example/', self.now, 'private'])
        value = read_live_history(self.catalogue, now=self.now)
        global_rows = {row.seconds: row for row in value.velocities if row.domain is None}
        self.assertEqual(global_rows[60].attempt_starts, 2)
        self.assertEqual(global_rows[300].attempt_starts, 3)
        self.assertEqual(global_rows[60].successful_captures, 1)
        self.assertEqual(global_rows[300].failed_captures, 1)
        self.assertEqual(global_rows[60].fulfillments, 3)
        self.assertEqual(global_rows[300].attempt_starts_per_minute, 0.6)
        self.assertNotIn('private.example', value.model_dump_json())
        self.assertNotIn('import.example', value.model_dump_json())
        self.assertNotIn('future.example', value.model_dump_json())
        self.assertEqual(len(value.recent), 2)
        self.assertTrue(all(item.query_ready is None for item in value.recent))

    def test_domain_and_recent_previews_are_bounded(self):
        for index in range(15):
            self.visit(domain=f'host{index}.example')
        value = read_live_history(self.catalogue, now=self.now)
        self.assertEqual(len(value.recent), 5)
        self.assertEqual(len(value.velocities), 22)
