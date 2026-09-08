"""Public policy admission is independent per feature, bounded, and versioned."""
from datetime import UTC, datetime, timedelta
import unittest

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from periplus.crawl.control.collections.schemas import CollectionSpec
from periplus.operations.access.models import PublicAccessRecord
from periplus.operations.access.schemas import AccessPolicy
from periplus.operations.access.service import AccessDenied, AccessStore


class PublicAccessTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        PublicAccessRecord.__table__.create(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.policy = AccessPolicy()
        self.policy.sql.requests = 1
        with self.sessions.begin() as session:
            session.add(PublicAccessRecord(id=1, configuration=self.policy.model_dump(mode='json')))
        self.store = AccessStore(self.sessions)
        self.now = datetime(2026, 9, 8, tzinfo=UTC)

    def test_independent_windows_and_retry_after(self):
        self.store.admit('sql', now=self.now)
        self.store.admit('assistant', now=self.now)
        with self.assertRaises(AccessDenied) as caught:
            self.store.admit('sql', now=self.now + timedelta(seconds=12))
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.headers['Retry-After'], '48')
        self.store.admit('sql', now=self.now + timedelta(seconds=60))
        with self.sessions() as session:
            windows = session.get(PublicAccessRecord, 1).windows
            self.assertEqual(set(windows), {'sql', 'assistant'})
            self.assertEqual(windows['sql']['count'], 1)

    def test_policy_edit_preserves_consumed_capacity_and_rejects_stale_save(self):
        self.store.admit('sql', now=self.now)
        self.assertEqual(self.store.save(self.policy, 1).version, 2)
        with self.assertRaises(AccessDenied) as caught:
            self.store.save(self.policy, 1)
        self.assertEqual(caught.exception.status_code, 409)
        with self.assertRaises(AccessDenied):
            self.store.admit('sql', now=self.now)

    def test_preparation_does_not_consume_but_disabled_gate_still_applies(self):
        for _ in range(3):
            self.store.admit('sql', consume=False, now=self.now)
        self.store.admit('sql', now=self.now)
        self.policy.sql.enabled = False
        self.store.save(self.policy, 1)
        with self.assertRaises(AccessDenied) as caught:
            self.store.admit('sql', consume=False)
        self.assertEqual(caught.exception.detail['code'], 'feature_disabled')
        self.store.admit('assistant', now=self.now)

    def test_unsupported_crawl_options_do_not_consume_quota(self):
        for change in ({'page_limit': 7}, {'max_depth': 3}, {'retention_seconds': 42}):
            with self.assertRaises(AccessDenied) as caught:
                self.store.admit('crawl', specification=CollectionSpec(**change), now=self.now)
            self.assertEqual(caught.exception.detail['code'], 'options_changed')
        with self.sessions() as session:
            self.assertEqual(session.get(PublicAccessRecord, 1).windows, {})
        self.store.admit('crawl', specification=CollectionSpec(), now=self.now)

    def test_defaults_must_be_allowed_and_options_unique(self):
        for change in ({'page_budgets': [5]}, {'max_depths': [0, 0, 1]}, {'retention_seconds': [0, None]}):
            with self.assertRaises(ValidationError):
                AccessPolicy.model_validate({'crawl': change})


import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from sqlalchemy import text


@unittest.skipUnless(os.environ.get('PERIPLUS_TEST_FRONTIER_POSTGRES') == '1', 'live Postgres opt-in')
class PublicAccessPostgresTests(unittest.TestCase):
    def test_global_allowance_cannot_be_overspent_by_concurrent_replicas(self):
        from periplus.platform.postgres.session import get_engine
        engine = get_engine()
        schema = 'access_test_' + uuid4().hex
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        try:
            scoped = engine.execution_options(schema_translate_map={None: schema})
            PublicAccessRecord.__table__.create(scoped)
            sessions = sessionmaker(scoped, expire_on_commit=False)
            policy = AccessPolicy()
            policy.sql.requests = 3
            with sessions.begin() as session:
                session.add(PublicAccessRecord(id=1, configuration=policy.model_dump(mode='json')))
            barrier = Barrier(8)
            def admit(_):
                barrier.wait(timeout=10)
                try:
                    AccessStore(sessions).admit('sql')
                    return 204
                except AccessDenied as error:
                    return error.status_code
            with ThreadPoolExecutor(max_workers=8) as workers:
                results = list(workers.map(admit, range(8)))
            self.assertEqual(results.count(204), 3)
            self.assertEqual(results.count(429), 5)
        finally:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
