"""Real frontier-backed collection API with service-role visibility and controls."""
import os
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from periplus.crawl.control.domain_policies.service import ensure_default_domain_policy

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from test_frontier_store import TABLES
from periplus.crawl.api.collections import router
from periplus.crawl.api.frontier import router as frontier_router
from periplus.crawl.runtime.frontier_models import FrontierControlRecord
from periplus.crawl.runtime.frontier_store import FrontierStore
from periplus.platform.api_access import ApiAccessMiddleware


class CollectionApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        for table in TABLES:
            table.create(self.engine)
        from periplus.operations.access.models import PublicAccessRecord
        from periplus.operations.access.schemas import AccessPolicy
        PublicAccessRecord.__table__.create(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(FrontierControlRecord(id=1))
            session.add(PublicAccessRecord(id=1, configuration=AccessPolicy().model_dump(mode="json")))
            ensure_default_domain_policy(session)
        app = FastAPI()
        app.state.frontier = FrontierStore(self.sessions)
        app.state.frontier_sessions = self.sessions
        from unittest.mock import AsyncMock
        self.history = AsyncMock()
        self.history.collection_readiness.return_value = {}
        self.history.get.return_value = None
        self.history.is_retired.return_value = False
        app.state.collection_history = self.history
        from periplus.crawl.runtime.frontier_health import CrawlerPresenceReader
        bucket = AsyncMock()
        from types import SimpleNamespace
        bucket.stream_info.return_value = SimpleNamespace(state=SimpleNamespace(subjects={}))
        app.state.crawler_presence = CrawlerPresenceReader(bucket)
        from periplus.crawl.api.domain_policies import router as domain_router
        from periplus.platform.postgres.session import get_session
        def database_session():
            with self.sessions.begin() as session:
                yield session
        app.dependency_overrides[get_session] = database_session
        app.include_router(domain_router)
        app.include_router(router)
        app.include_router(frontier_router)
        app.add_middleware(ApiAccessMiddleware)
        env = patch.dict(os.environ, {"PERIPLUS_ADMIN_API_TOKEN": "admin", "PERIPLUS_PUBLIC_API_TOKEN": "public"})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(app, headers={"Authorization": "Bearer public"})
        self.addCleanup(self.client.close)
        self.admin = {"Authorization": "Bearer admin"}
        self.payload = {"id": str(uuid4()), "specification": {"seed_urls": ["https://example.com/"]}}

    def test_public_gates_do_not_block_admin_or_identical_replays(self):
        from periplus.operations.access.service import AccessStore
        from periplus.operations.access.schemas import AccessPolicy
        first = self.client.post('/collections', json=self.payload)
        self.assertEqual(first.status_code, 201, first.text)
        store = AccessStore(self.sessions)
        policy = AccessPolicy()
        policy.crawl.enabled = False
        store.save(policy, 1)
        self.assertEqual(self.client.post('/collections', json=self.payload).status_code, 201)
        fresh = {'specification': self.payload['specification']}
        self.assertEqual(self.client.post('/collections', json=fresh).status_code, 403)
        admin = self.client.post('/collections', json=fresh, headers=self.admin)
        self.assertEqual(admin.status_code, 201, admin.text)
        self.assertEqual(admin.json()['specification']['request_class'], 'admin')
        self.assertEqual(len(self.client.get('/collections').json()['items']), 2)

    def test_request_class_filters_before_pagination(self):
        from datetime import UTC, datetime, timedelta
        from periplus.crawl.control.collections.schemas import CollectionSpec

        from periplus.crawl.control.collections.models import CollectionRecord

        expected = []
        now = datetime.now(UTC)
        for index, request_class in enumerate(["public"] * 7 + ["admin"] * 6 + ["system"] * 6):
            identity = uuid4()
            self.client.app.state.frontier.create_collection(
                identity, CollectionSpec(seed_urls=("https://example.com/",), request_class=request_class))
            with self.sessions.begin() as session:
                session.get(CollectionRecord, identity).created_at = now + timedelta(seconds=index)
            if request_class == "public":
                expected.insert(0, str(identity))
        unfiltered = self.client.get("/collections?limit=6").json()["items"]
        self.assertEqual([item["specification"]["request_class"] for item in unfiltered], ["system"] * 6)
        for offset in (0, 5):
            response = self.client.get(f"/collections?request_class=public&limit=5&offset={offset}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([item["id"] for item in response.json()["items"]], expected[offset:offset + 5])

    def test_retired_request_cannot_be_recreated_and_retention_defaults_forever(self):
        self.history.is_retired.return_value = True
        self.assertEqual(self.client.post("/collections", json=self.payload).status_code, 410)
        self.assertIsNone(self.client.app.state.frontier.get_collection(UUID(self.payload["id"])))
        self.history.is_retired.return_value = False
        result = self.client.post("/collections", json=self.payload).json()
        self.assertIsNone(result["specification"]["retention_seconds"])
        self.assertIsNone(result["expires_at"])
        self.assertFalse(result["retention_expired"])

    def test_submission_is_durable_and_idempotent_without_running_work(self):
        for _ in range(2):
            response = self.client.post("/collections", json=self.payload)
            self.assertEqual(response.status_code, 201, response.text)
            value = response.json()
            self.assertEqual(value["consumed_pages"], 0)
            self.assertFalse(value["seeds_settled"])
            self.assertIsNone(value["query_ready"])
        self.assertEqual(len(self.client.get("/collections").json()["items"]), 1)
        self.assertEqual(self.client.get(f'/collections/{self.payload["id"]}').status_code, 200)

    def test_public_class_is_assigned_and_controls_require_admin(self):
        supplied = self.payload | {"specification": {"seed_urls": ["https://example.com/"], "request_class": "system"}}
        response = self.client.post("/collections", json=supplied)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["specification"]["request_class"], "public")
        path = f'/collections/{supplied["id"]}/actions'
        self.assertEqual(self.client.post(path, json={"action": "cancel"}).status_code, 403)
        self.assertEqual(self.client.post(path, json={"action": "pause"}, headers=self.admin).json()["status"], "paused")
        self.assertEqual(self.client.post(path, json={"action": "resume"}, headers=self.admin).json()["status"], "active")
        self.assertEqual(self.client.post(path, json={"action": "cancel"}, headers=self.admin).json()["outcome"], "cancelled")

    def test_capacity_rejects_new_intent_but_allows_identical_retry(self):
        self.assertEqual(self.client.post("/collections", json=self.payload).status_code, 201)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).pending_count = 10000
        rejected = self.client.post("/collections", json=self.payload | {"id": str(uuid4())})
        self.assertEqual(rejected.status_code, 429)
        self.assertEqual(rejected.json()["detail"]["code"], "crawl_queue_full")
        self.assertEqual(rejected.headers["retry-after"], "5")
        self.assertEqual(self.client.post("/collections", json=self.payload | {"id": str(uuid4())}, headers=self.admin).status_code, 201)
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).pending_count = 9999
        self.assertEqual(self.client.post("/collections", json=self.payload | {"id": str(uuid4())}).status_code, 201)
        self.assertEqual(self.client.post("/collections", json=self.payload).status_code, 201)

    def test_invalid_rules_limits_and_oversized_transport_are_rejected(self):
        for specification in ({}, {"seed_urls": ["https://example.com/"], "follow_sql": "SELECT * FROM web.observation"}):
            self.assertEqual(self.client.post("/collections", json={"specification": specification}).status_code, 422)
        self.assertEqual(self.client.post("/collections", content=b"x" * (512 * 1024 + 1)).status_code, 413)
        self.assertEqual(self.client.get("/collections?limit=101").status_code, 422)
        self.assertEqual(self.client.get(f"/collections/{uuid4()}").status_code, 404)

    def test_control_app_exposes_collections_without_graph_submission_routes(self):
        from periplus.entrypoints.api import app
        paths = set(app.openapi()["paths"])
        self.assertIn("/collections", paths)
        self.assertFalse(any(path.startswith(("/graph", "/crawl-plans", "/coverage-requests", "/crawls")) for path in paths))

    def test_description_submission_reports_discovery_without_running_it(self):
        response = self.client.post("/collections", json={"specification": {"seed_description": "Finnish robotics"}})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["discovery_stage"], "planning")
        self.assertEqual(response.json()["resolved_urls"], [])
        self.assertEqual(response.json()["reserved_pages"], 0)

    def test_frontier_controls_are_admin_only_versioned_and_do_not_dispatch(self):
        path = "/frontier/controls"
        self.assertEqual(self.client.get(path).status_code, 403)
        initial = self.client.get(path, headers=self.admin)
        self.assertEqual(initial.status_code, 200, initial.text)
        value = initial.json()
        change = {"expected_version": value["policy_version"],
                  "settings": value["settings"] | {"paused": True}}
        self.assertEqual(self.client.put(path, json=change).status_code, 403)
        updated = self.client.put(path, json=change, headers=self.admin)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertTrue(updated.json()["settings"]["paused"])
        self.assertEqual(updated.json()["updated_by"], "admin_service")
        self.assertEqual(updated.json()["dispatched_acquisitions"], 0)
        self.assertEqual(self.client.put(path, json=change, headers=self.admin).status_code, 409)
        bad = change | {"settings": value["settings"] | {"dispatch_limit": 0}}
        self.assertEqual(self.client.put(path, json=bad, headers=self.admin).status_code, 422)

    def test_priority_changes_require_admin_and_preserve_frozen_intent(self):
        original = self.client.post("/collections", json=self.payload).json()
        path = f'/collections/{self.payload["id"]}/priority'
        self.assertEqual(self.client.put(path, json={"priority": 5}).status_code, 403)
        response = self.client.put(path, json={"priority": 5}, headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["priority"], 5)
        self.assertEqual(response.json()["specification"], original["specification"])
        self.client.post(f'/collections/{self.payload["id"]}/actions',
                         json={"action": "cancel"}, headers=self.admin)
        self.assertEqual(self.client.put(path, json={"priority": 0}, headers=self.admin).status_code, 409)


    def historical(self, *, private=False):
        from datetime import UTC, datetime
        from periplus.crawl.control.collections.history import HistoricalCollection
        from periplus.crawl.control.collections.schemas import CollectionExecutionSpec
        return HistoricalCollection(id=self.payload["id"], specification=CollectionExecutionSpec(
            **self.payload["specification"], request_class="admin" if private else "public"),
            created_at=datetime.now(UTC), completed_at=datetime.now(UTC), outcome="page_limit",
            consumed_pages=1, supplied_pages=1, failed_pages=0, seed_provenance=None, as_of=datetime.now(UTC))

    def test_current_and_historical_details_report_proven_collection_readiness(self):
        from datetime import UTC, datetime
        from uuid import UUID
        from periplus.materialization.readiness import CollectionReadiness
        from periplus.crawl.control.collections.history import HistoryUnavailable
        identity, generation, now = UUID(self.payload['id']), uuid4(), datetime.now(UTC)
        self.history.collection_readiness.return_value = {identity: CollectionReadiness(
            collection_id=identity, query_ready=True, reason='active_generation_committed',
            generation_id=generation, as_of=now)}
        self.history.get.return_value = self.historical()
        value = self.client.get(f'/collections/{identity}').json()
        self.assertTrue(value['query_ready'])
        self.assertEqual(value['query_generation_id'], str(generation))
        self.assertIsNotNone(value['query_readiness_as_of'])
        self.history.get.return_value = None
        self.client.post('/collections', json=self.payload)
        self.client.post(f'/collections/{identity}/actions', json={'action': 'cancel'}, headers=self.admin)
        value = self.client.get(f'/collections/{identity}').json()
        self.assertTrue(value['query_ready'])
        self.assertEqual(value['source'], 'current')
        self.assertTrue(self.client.get('/collections').json()['items'][0]['query_ready'])
        self.history.collection_readiness.side_effect = HistoryUnavailable('secret storage detail')
        response = self.client.get(f'/collections/{identity}')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['query_ready'])
        self.assertEqual(response.json()['query_readiness_reason'], 'catalogue_readiness_unavailable')
        self.assertNotIn('secret storage detail', response.text)

    def test_historical_detail_and_identical_submission_do_not_recreate_control_state(self):
        self.history.get.return_value = self.historical()
        detail = self.client.get(f'/collections/{self.payload["id"]}')
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["source"], "history")
        self.assertIsNone(detail.json()["query_ready"])
        replay = self.client.post("/collections", json=self.payload)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json()["source"], "history")
        self.assertEqual(self.client.get("/collections").json()["items"], [])
        conflict = self.client.post("/collections", json=self.payload | {"specification": self.payload["specification"] | {"page_limit": 2}})
        self.assertEqual(conflict.status_code, 409)

    def test_admin_history_is_shared_but_public_cannot_replace_its_intent(self):
        self.history.get.return_value = self.historical(private=True)
        detail = self.client.get(f'/collections/{self.payload["id"]}')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["specification"]["request_class"], "admin")
        self.assertEqual(self.client.post("/collections", json=self.payload).status_code, 409)
        self.assertEqual(self.client.get("/collections").json()["items"], [])

    def test_history_outage_defers_supplied_identity_but_new_server_identity_can_start(self):
        from periplus.crawl.control.collections.history import HistoryUnavailable
        self.history.get.side_effect = HistoryUnavailable("offline")
        self.assertEqual(self.client.post("/collections", json=self.payload).status_code, 503)
        self.assertEqual(self.client.get(f'/collections/{self.payload["id"]}').status_code, 503)
        created = self.client.post("/collections", json={"specification": self.payload["specification"]})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["source"], "current")
        retry = self.client.post("/collections", json=self.payload | {"id": created.json()["id"]})
        self.assertEqual(retry.status_code, 201, retry.text)

    def test_retiring_current_record_uses_history_without_recreating_or_exposing_partial_counts(self):
        from periplus.crawl.control.collections.models import CollectionRecord
        from uuid import UUID
        self.assertEqual(self.client.post("/collections", json=self.payload).status_code, 201)
        with self.sessions.begin() as session:
            record = session.get(CollectionRecord, UUID(self.payload["id"]))
            record.status = "settled"
            record.retiring = True
        self.history.get.return_value = self.historical()
        self.assertEqual(self.client.get("/collections").json()["items"], [])
        self.assertEqual(self.client.get(f'/collections/{self.payload["id"]}').json()["source"], "history")
        replay = self.client.post("/collections", json=self.payload)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json()["source"], "history")

    def test_replay_racing_current_record_removal_returns_history_without_recreating(self):
        from sqlalchemy import delete
        from periplus.crawl.control.collections.models import CollectionRecord
        from periplus.crawl.runtime.frontier_models import FrontierOutboxRecord
        self.assertEqual(self.client.post("/collections", json=self.payload).status_code, 201)
        self.history.get.return_value = self.historical()
        frontier = self.client.app.state.frontier
        original = frontier.get_collection
        def remove_after_read(identity):
            record = original(identity)
            with self.sessions.begin() as session:
                session.execute(delete(FrontierOutboxRecord).where(FrontierOutboxRecord.collection_id == identity))
                session.execute(delete(CollectionRecord).where(CollectionRecord.id == identity))
            return record
        with patch.object(frontier, "get_collection", side_effect=remove_after_read):
            replay = self.client.post("/collections", json=self.payload)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json()["source"], "history")
        self.assertEqual(self.client.get("/collections").json()["items"], [])

    def test_history_route_precedes_identity_route_and_enforces_visibility(self):
        from datetime import UTC, datetime
        from periplus.crawl.control.collections.history import CollectionHistoryPage, HistoricalCollectionSummary, HistoryUnavailable
        page = CollectionHistoryPage(items=[], next_cursor=None, as_of=datetime.now(UTC))
        self.history.list.return_value = page
        response = self.client.get('/collections/history?limit=7')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['source'], 'history')
        self.history.list.assert_awaited_with( limit=7, cursor=None)
        self.client.get('/collections/history', headers=self.admin)
        self.history.list.assert_awaited_with( limit=20, cursor=None)
        self.history.list.return_value = page.model_copy(update={'items': [HistoricalCollectionSummary(
            id=uuid4(), request_class='admin', summary='Secret', created_at=datetime.now(UTC),
            completed_at=None, outcome=None, consumed_pages=None, supplied_pages=None, failed_pages=None)]})
        response = self.client.get('/collections/history')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['items'][0]['request_class'], 'admin')
        self.history.list.side_effect = ValueError('invalid cursor')
        self.assertEqual(self.client.get('/collections/history?cursor=bad').status_code, 422)
        self.history.list.side_effect = HistoryUnavailable('storage credentials')
        response = self.client.get('/collections/history')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers['Retry-After'], '5')
        self.assertNotIn('credentials', response.text)

    def test_domain_policy_controls_are_admin_only_and_reject_stale_updates(self):
        payload = {"slug": "example", "host_match": "example.com", "paused": True}
        self.assertEqual(self.client.post('/domain-policies/', json=payload).status_code, 403)
        created = self.client.post('/domain-policies/', json=payload, headers=self.admin)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertTrue(created.json()['paused'])
        self.assertEqual(created.json()['updated_by'], 'admin')
        path = '/domain-policies/' + created.json()['id']
        update = self.client.patch(path, json={'expected_version': 1, 'paused': False,
                                   'maximum_concurrency': 2, 'minimum_request_interval_seconds': 0.5}, headers=self.admin)
        self.assertEqual(update.status_code, 200, update.text)
        self.assertEqual(update.json()['version'], 2)
        self.assertFalse(update.json()['paused'])
        self.assertEqual(self.client.patch(path, json={'expected_version': 1, 'paused': True}, headers=self.admin).status_code, 409)
        self.assertEqual(self.client.delete(path + '?expected_version=1', headers=self.admin).status_code, 409)
        self.assertEqual(self.client.delete(path + '?expected_version=2', headers=self.admin).status_code, 204)


    def test_current_item_routes_share_classes_and_enforce_bounds(self):
        from frontier_fixtures import policy_snapshot
        from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
        from periplus.crawl.control.collections.schemas import SelectionContext
        self.client.post("/collections", json=self.payload)
        identity = UUID(self.payload["id"])
        self.client.app.state.frontier.admit(identity, "https://example.com/",
            SelectionContext(depth=0, rule_id="seed"), EffectivePolicySnapshot.model_validate(policy_snapshot()))
        page = self.client.get(f"/collections/{identity}/items?limit=1")
        self.assertEqual(page.status_code, 200, page.text)
        acquisition = page.json()["items"][0]["acquisition"]["id"]
        detail = self.client.get(f"/frontier/items/{acquisition}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["callers"][0]["collection_id"], str(identity))
        self.assertEqual(self.client.get(f"/collections/{identity}/items?limit=101").status_code, 422)
        self.assertEqual(self.client.get(f"/collections/{identity}/items?after=invalid").status_code, 422)
        private = self.client.post("/collections", headers=self.admin,
            json={"specification": {"seed_urls": ["https://private.example/"], "request_class": "admin"}}).json()
        self.assertEqual(self.client.get(f"/collections/{private['id']}/items").status_code, 200)
        self.assertEqual(self.client.get(f"/frontier/items/{uuid4()}").status_code, 404)
        self.assertEqual(self.client.post(f"/frontier/items/{acquisition}").status_code, 403)

    def test_item_readiness_is_batched_visible_and_degrades_without_hiding_current_work(self):
        from datetime import UTC, datetime
        from frontier_fixtures import policy_snapshot
        from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
        from periplus.crawl.control.collections.schemas import SelectionContext
        from periplus.crawl.control.collections.history import HistoryUnavailable
        from periplus.crawl.runtime.frontier_models import AcquisitionRecord
        from periplus.materialization.readiness import ObservationReadiness
        identity = UUID(self.payload['id'])
        self.client.post('/collections', json=self.payload)
        self.client.app.state.frontier.admit(identity, 'https://example.com/',
            SelectionContext(depth=0, rule_id='seed'), EffectivePolicySnapshot.model_validate(policy_snapshot()))
        page = self.client.get(f'/collections/{identity}/items').json()
        acquisition = UUID(page['items'][0]['acquisition']['id'])
        self.history.readiness.assert_not_awaited()
        with self.sessions.begin() as session:
            record = session.get(AcquisitionRecord, acquisition)
            record.status = 'succeeded'
            record.outcome = {'visit': {'visit_id': str(acquisition)}}
        for ready, reason in ((False, 'materialization_pending'), (True, 'active_generation_committed')):
            self.history.readiness.return_value = {acquisition: ObservationReadiness(
                observation_id=acquisition, query_ready=ready, reason=reason, as_of=datetime.now(UTC))}
            page = self.client.get(f'/collections/{identity}/items').json()
            self.assertIs(page['items'][0]['acquisition']['query_ready'], ready)
            self.assertTrue(page['items'][0]['acquisition']['evidence_committed'])
            self.history.readiness.assert_awaited_with([acquisition])
            detail = self.client.get(f'/frontier/items/{acquisition}', headers=self.admin).json()
            self.assertIs(detail['query_ready'], ready)
            self.history.readiness.assert_awaited_with([acquisition])
        self.history.readiness.side_effect = HistoryUnavailable('private storage detail')
        response = self.client.get(f'/frontier/items/{acquisition}')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()['query_ready'])
        self.assertEqual(response.json()['query_readiness_reason'], 'catalogue_readiness_unavailable')
        self.assertNotIn('private storage', response.text)

    def test_arrivals_distinguish_not_yet_ingested_current_intent_from_missing_history(self):
        self.history.arrivals.return_value = None
        identity = self.payload['id']
        self.client.post('/collections', json=self.payload)
        response = self.client.get(f'/collections/{identity}/arrivals')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['definition_committed'])
        self.assertEqual(response.json()['items'], [])
        self.assertEqual(self.client.get(f'/collections/{uuid4()}/arrivals').status_code, 404)
        self.assertEqual(self.client.get(f'/collections/{identity}/arrivals?limit=101').status_code, 422)
        from periplus.crawl.control.collections.history import HistoryUnavailable
        self.history.arrivals.side_effect = HistoryUnavailable('offline')
        unavailable = self.client.get(f'/collections/{identity}/arrivals')
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.headers['retry-after'], '5')

    def test_public_capture_feed_is_read_only_cursor_validated_and_independent_of_history(self):
        self.history.live.side_effect = AssertionError('capture feed must not read history')
        first = self.client.get('/frontier/captures')
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()['bootstrap'])
        second = self.client.get('/frontier/captures', params={'cursor': first.json()['cursor']})
        self.assertEqual(second.status_code, 200, second.text)
        self.assertFalse(second.json()['bootstrap'])
        self.assertEqual(second.json()['items'], [])
        self.assertEqual(self.client.get('/frontier/captures?cursor=bad').status_code, 422)
        self.assertEqual(self.client.post('/frontier/captures').status_code, 403)

    def test_public_live_keeps_current_activity_when_history_is_unavailable(self):
        from periplus.crawl.control.collections.history import HistoryUnavailable
        self.history.live.side_effect = HistoryUnavailable('private storage details')
        response = self.client.get('/frontier/live')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['current']['queued'], 0)
        self.assertIsNone(response.json()['history'])
        self.assertEqual(response.json()['history_unavailable_reason'], 'catalogue_history_unavailable')
        self.assertNotIn('private storage details', response.text)

    def test_live_recent_merge_prefers_committed_evidence_without_duplicate_observations(self):
        from datetime import UTC, datetime
        from unittest.mock import patch
        from periplus.crawl.runtime.live import CurrentActivity, HistoricalActivity, RecentCapture
        identity, now = uuid4(), datetime.now(UTC)
        recent = RecentCapture(observation_id=identity, requested_url='https://example.com/',
                               completed_at=now, evidence_committed=False)
        current = CurrentActivity(as_of=now, paused=False, queued=0, dispatched=0, started=0,
            oldest_wait_at=None, domains=[], more_domains=False, upcoming=[], recent=[recent])
        self.history.live.return_value = HistoricalActivity(as_of=now, window_end=now, velocities=[],
            recent=[recent.model_copy(update={'evidence_committed': True})])
        with patch('periplus.crawl.api.frontier.current_activity', return_value=current):
            response = self.client.get('/frontier/live')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['recent']), 1)
        self.assertTrue(response.json()['recent'][0]['evidence_committed'])
        self.assertIsNone(response.json()['recent'][0]['query_ready'])

    def test_observation_lineage_preserves_role_and_distinguishes_absence_from_failure(self):
        from datetime import UTC, datetime
        from periplus.crawl.control.collections.history import HistoryUnavailable
        from periplus.crawl.control.collections.lineage import ObservationLineagePage
        identity = uuid4()
        path = f'/frontier/observations/{identity}/lineage'
        self.history.observation_lineage.return_value = ObservationLineagePage(
            observation_id=identity, requested_url='https://example.com/', items=[],
            next_cursor=None, as_of=datetime.now(UTC))
        response = self.client.get(path, params={'limit': 3, 'cursor': 'opaque'})
        self.assertEqual(response.status_code, 200, response.text)
        self.history.observation_lineage.assert_awaited_with(identity,  limit=3, cursor='opaque')
        self.assertEqual(self.client.get(path, headers=self.admin).status_code, 200)
        self.history.observation_lineage.assert_awaited_with(identity,  limit=20, cursor=None)
        self.history.observation_lineage.return_value = None
        self.assertEqual(self.client.get(path).status_code, 404)
        self.history.observation_lineage.side_effect = HistoryUnavailable('sensitive internal storage error')
        response = self.client.get(path)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers['retry-after'], '5')
        self.assertNotIn('sensitive', response.text)
        self.history.observation_lineage.reset_mock()
        self.assertEqual(self.client.get(path, params={'limit': 101}).status_code, 422)
        self.assertEqual(self.client.get(path, params={'cursor': 'x' * 513}).status_code, 422)
        self.history.observation_lineage.assert_not_awaited()

    def test_duration_is_relative_and_absolute_deadline_is_not_an_input(self):
        from datetime import UTC, datetime, timedelta
        payload = self.payload | {"specification": {"seed_urls": ["https://example.com/"], "max_duration_seconds": 30}}
        first = self.client.post("/collections", json=payload)
        self.assertEqual(first.status_code, 201, first.text)
        value = first.json()
        self.assertEqual(datetime.fromisoformat(value["specification"]["deadline_at"]),
                         datetime.fromisoformat(value["created_at"]).replace(tzinfo=UTC) + timedelta(seconds=30))
        replay = self.client.post("/collections", json=payload)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json()["specification"]["deadline_at"], value["specification"]["deadline_at"])
        self.assertEqual(self.client.post("/collections", json=self.payload | {"specification": payload["specification"] | {"deadline_at": value["specification"]["deadline_at"]}}).status_code, 422)
