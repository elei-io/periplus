"""SDK contracts exercised through the actual ASGI control API."""
from datetime import UTC, datetime
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'periplus-python-sdk/src'))
from periplus_sdk import collections, configure, frontier
from periplus_sdk.errors import ConflictError, AuthenticationError
from periplus_sdk.types import CollectionSpec, DomainPolicyCreateRequest, DomainPolicyUpdateRequest, HistoricalCollection as SdkHistory
from periplus_sdk.conn._common import PUBLIC_RELATIONS, PUBLIC_CATALOGUE_VERSION
from test_frontier_store import TABLES
from periplus.crawl.api.collections import router as collection_router
from periplus.crawl.api.frontier import router as frontier_router
from periplus.crawl.api.domain_policies import router as domain_router
from periplus.crawl.control.collections.history import HistoricalCollection, CollectionHistoryPage, HistoricalCollectionSummary
from periplus.crawl.control.domain_policies.service import ensure_default_domain_policy
from periplus.crawl.runtime.frontier_models import FrontierControlRecord
from periplus.crawl.runtime.frontier_store import FrontierStore
from periplus.platform.api_access import ApiAccessMiddleware
from periplus.platform.postgres.session import get_session


class SdkCollectionApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        for table in TABLES:
            table.create(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(FrontierControlRecord(id=1))
            ensure_default_domain_policy(session)
        self.store = FrontierStore(self.sessions)
        self.history = AsyncMock()
        self.history.collection_readiness.return_value = {}
        self.history.get.return_value = None
        app = FastAPI()
        app.state.frontier = self.store
        app.state.frontier_sessions = self.sessions
        app.state.collection_history = self.history
        from periplus.crawl.runtime.frontier_health import CrawlerPresenceReader
        bucket = AsyncMock()
        from types import SimpleNamespace
        bucket.stream_info.return_value = SimpleNamespace(state=SimpleNamespace(subjects={}))
        app.state.crawler_presence = CrawlerPresenceReader(bucket)
        for router in (collection_router, frontier_router, domain_router):
            app.include_router(router)
        def database_session():
            with self.sessions.begin() as session:
                yield session
        app.dependency_overrides[get_session] = database_session
        app.add_middleware(ApiAccessMiddleware)
        env = patch.dict(os.environ, {'PERIPLUS_ADMIN_API_TOKEN': 'admin', 'PERIPLUS_PUBLIC_API_TOKEN': 'public'})
        env.start()
        self.addCleanup(env.stop)
        configure(api_url='http://test', api_token='admin')
        client_type = httpx.AsyncClient
        transport = httpx.ASGITransport(app=app)
        client_patch = patch('periplus_sdk._http.httpx.AsyncClient', side_effect=lambda **kwargs:
                             client_type(**kwargs, transport=transport))
        client_patch.start()
        self.addCleanup(client_patch.stop)

    async def test_submission_controls_and_current_pagination(self):
        collection = await collections.submit(CollectionSpec(seed_urls=('HTTPS://example.com/#fragment',), page_limit=2))
        self.assertEqual(collection.snapshot.specification.seed_urls, ('https://example.com/',))
        await collection.pause()
        self.assertEqual(collection.snapshot.status, 'paused')
        await collection.resume()
        await collection.set_priority(3)
        self.assertEqual(collection.snapshot.priority, 3)
        page = await collections.list(status='active', limit=1)
        self.assertEqual([item.id for item in page.items], [collection.id])
        await collection.cancel()
        self.assertTrue(collection.settled)
        self.assertEqual(collection.snapshot.outcome, 'cancelled')
        self.assertIsNone(collection.snapshot.query_ready)
        self.assertEqual(self.store.control_view().started_attempts, 0)

    async def test_history_replay_and_browsing(self):
        identity, now = uuid4(), datetime.now(UTC)
        spec = CollectionSpec(seed_urls=('https://example.com/',))
        self.history.get.return_value = HistoricalCollection(id=identity, specification=spec.model_dump(mode='json'),
            created_at=now, completed_at=now, outcome='budget_reached', consumed_pages=1, supplied_pages=1,
            failed_pages=0, seed_provenance=None, as_of=now)
        self.history.list.return_value = CollectionHistoryPage(items=[HistoricalCollectionSummary(
            id=identity, visibility='public', summary='Example', created_at=now, completed_at=now,
            outcome='budget_reached', consumed_pages=1, supplied_pages=1, failed_pages=0)], next_cursor=None, as_of=now)
        collection = await collections.get(identity)
        self.assertIsInstance(collection.snapshot, SdkHistory)
        self.assertTrue(collection.settled)
        self.assertEqual((await collections.submit(spec, id=identity)).id, identity)
        self.assertIsNone(self.store.get_collection(identity))
        page = await collections.history(limit=1)
        self.assertEqual(page.items[0].id, identity)
        self.assertIsNone(page.next_cursor)

    async def test_versioned_controls_and_domains(self):
        initial = await frontier.controls()
        updated = await frontier.replace_controls(initial.settings.model_copy(update={'paused': True, 'background_share': 7}),
                                                   expected_version=initial.policy_version)
        self.assertTrue(updated.settings.paused)
        self.assertEqual(updated.settings.background_share, 7)
        with self.assertRaises(ConflictError):
            await frontier.replace_controls(initial.settings, expected_version=initial.policy_version)
        domain = await frontier.create_domain(DomainPolicyCreateRequest(slug='example', host_match='example.com', paused=True))
        changed = await frontier.update_domain(domain.id, DomainPolicyUpdateRequest(expected_version=domain.version,
                                                                                   paused=False, maximum_concurrency=2))
        self.assertEqual(changed.version, domain.version + 1)
        self.assertFalse(changed.paused)
        self.assertEqual(len((await frontier.domains(match_pattern='example.com')).items), 1)
        await frontier.delete_domain(domain.id, expected_version=changed.version)
        configure(api_url='http://test', api_token='public')
        with self.assertRaises(AuthenticationError):
            await frontier.controls()

    def test_catalogue_contract_matches_backend(self):
        from periplus.platform.catalogue.public import PUBLIC_CATALOGUE_VERSION as backend_version
        from periplus.platform.catalogue.public_registry import PUBLIC_OBJECTS
        self.assertEqual(PUBLIC_CATALOGUE_VERSION, backend_version)
        self.assertEqual(PUBLIC_RELATIONS, {(view.schema, view.name) for view in PUBLIC_OBJECTS if view.kind == "view"})

    async def test_sdk_current_items_arrivals_and_live_use_real_http_contracts(self):
        from frontier_fixtures import policy_snapshot
        from periplus.crawl.control.collections.schemas import SelectionContext
        from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
        from periplus.crawl.control.collections.history import HistoryUnavailable
        collection = await collections.submit(CollectionSpec(seed_urls=('https://example.com/',)))
        admitted = self.store.admit(collection.id, 'https://example.com/',
            SelectionContext(depth=0, rule_id='seed'), EffectivePolicySnapshot.model_validate(policy_snapshot()))
        page = await collection.items(limit=1)
        self.assertEqual(page.collection_id, collection.id)
        self.assertEqual(page.items[0].acquisition.id, admitted.acquisition_id)
        item = await frontier.item(admitted.acquisition_id)
        self.assertEqual(item.callers[0].collection_id, collection.id)
        self.assertIsNone(item.query_ready)
        self.history.arrivals.return_value = None
        arrivals = await collection.arrivals()
        self.assertFalse(arrivals.definition_committed)
        self.assertEqual(arrivals.items, [])
        self.history.live.side_effect = HistoryUnavailable('offline')
        live = await frontier.live()
        self.assertEqual(live.current.queued, 1)
        self.assertIsNone(live.history)
        self.assertIsNone(live.current.next_start_estimate)
        self.assertEqual(self.store.control_view().started_attempts, 0)

        from periplus.crawl.runtime.live import HistoricalActivity, VelocityWindow
        from periplus.crawl.control.collections.arrivals import CollectionArrivalsPage, CollectionArrival
        now = datetime.now(UTC)
        self.history.live.side_effect = None
        self.history.live.return_value = HistoricalActivity(as_of=now, window_end=now, recent=[],
            velocities=[VelocityWindow(seconds=60, domain=None, attempt_starts=2, successful_captures=1,
                failed_captures=0, fulfillments=3, attempt_starts_per_minute=2)])
        recovered = await frontier.live()
        self.assertEqual(recovered.history.velocities[0].attempt_starts, 2)
        self.assertEqual(recovered.history.velocities[0].fulfillments, 3)
        self.history.arrivals.return_value = CollectionArrivalsPage(collection_id=collection.id,
            items=[CollectionArrival(fulfillment_id=uuid4(), observation_id=admitted.acquisition_id,
                requested_url='https://example.com/', parent_observation_id=None, depth=0, rule_id='seed',
                mode='shared', decided_at=now, observation_committed=True, effective_url='https://example.com/',
                observed_at=now, outcome='succeeded', http_status_code=200)], next_cursor='opaque', as_of=now)
        durable = await collection.arrivals(limit=1)
        self.assertEqual(durable.next_cursor, 'opaque')
        self.assertTrue(durable.items[0].observation_committed)
        self.assertIsNone(durable.items[0].query_ready)
