from periplus.operations.access.schemas import QueryLimits
from periplus.query.binding import ExecutionContext, PublicationBinding
import asyncio
from datetime import UTC, datetime, timedelta
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from periplus.query.history import HistoryClient, VERSION, shape, track
from periplus.query.models import QueryRequest
from periplus.query.server_http import _run
from periplus.operations.query_history.schemas import Execution
from periplus.operations.query_history.models import QueryExecution
from periplus.operations.query_history.store import QueryHistoryStore
from periplus.operations.api.query_history import router
from periplus.platform.api_access import ApiAccessMiddleware


def execution(**overrides):
    now = datetime.now(UTC)
    data = dict(execution_id=uuid4(), started_at=now, finished_at=now, source='public_console', operation='execute',
                sql_text='select 1', parameters=[], fingerprint_version=VERSION, outcome='success', elapsed_ms=100,
                **shape('select 1'))
    data.update(overrides)
    return Execution(**data)

class ShapeTests(unittest.TestCase):
    def test_literals_comments_and_parameters_group_without_losing_structure(self):
        a = shape("SELECT * FROM web.observation WHERE domain='sensitive' LIMIT 10 -- private")
        b = shape('select * from web.observation where domain=? limit 99')
        self.assertEqual(a['query_fingerprint'], b['query_fingerprint'])
        self.assertNotIn('sensitive', a['query_template'])
        self.assertNotIn('private', a['query_template'])
        self.assertEqual(a['relations'], ['web.observation'])
        self.assertNotEqual(a['query_fingerprint'], shape('select count(*) from web.observation')['query_fingerprint'])

    def test_malformed_and_ctes(self):
        self.assertIsNone(shape('select (')['query_template'])
        result = shape('with x as (select * from web.observation) select count(*) from x')
        self.assertEqual(result['relations'], ['web.observation'])
        self.assertEqual(result['features']['ctes'], 1)
        self.assertIn('count', result['functions'])
        self.assertEqual(shape('select * from web.observation a join web.observation b on a.id=b.id')['relations'], ['web.observation'])

class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_does_not_escape(self):
        client = HistoryClient()
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
        await client.record(execution())
        await client.close()

    async def test_track_preserves_raw_payload_and_request_correlation(self):
        recorder = SimpleNamespace(record=AsyncMock())
        identity = uuid4()
        request = SimpleNamespace(state=SimpleNamespace(request_id=str(identity)), headers={},
            app=SimpleNamespace(state=SimpleNamespace(query_history=recorder)))
        async with track(request, QueryRequest(sql='select ?', parameters=['private']), 'execute') as record:
            record.update(outcome='success', error_code=None)
        value = recorder.record.call_args.args[0]
        self.assertEqual(value.parameters, ['private'])
        self.assertEqual(value.sql_text, 'select ?')
        self.assertEqual(value.request_id, identity)
        self.assertEqual(value.source, 'unknown')

    async def test_capacity_and_timeout_are_terminal_history(self):
        recorder = SimpleNamespace(record=AsyncMock())
        slot = asyncio.Semaphore(1)
        def timeout(payload, *, limits, evidence, publication):
            evidence.plan = 'estimated scan'
            evidence.plan_truncated = False
            evidence.engine_version = 'test-engine'
            raise TimeoutError()
        request = SimpleNamespace(state=SimpleNamespace(), headers={}, app=SimpleNamespace(state=SimpleNamespace(
            query_history=recorder, query_slot=slot, query_limits=SimpleNamespace(read=AsyncMock(return_value=ExecutionContext(limits=QueryLimits(), publication=PublicationBinding(expires_at="2099-01-01T00:00:00Z", database="public_v1", revision=0)))), query_service=SimpleNamespace(execute=timeout, compiler_version="public-query-v4:experimental"))))
        await slot.acquire()
        self.assertEqual((await _run(request, QueryRequest(sql='select 1'), 'execute')).status_code, 429)
        self.assertEqual(recorder.record.call_args.args[0].outcome, 'rejected')
        self.assertEqual(recorder.record.call_args.args[0].compiler_version, 'public-query-v4:experimental')
        self.assertIsNone(recorder.record.call_args.args[0].plan)
        slot.release()
        self.assertEqual((await _run(request, QueryRequest(sql='select 1'), 'execute')).status_code, 408)
        self.assertEqual(recorder.record.call_args.args[0].outcome, 'timeout')
        self.assertEqual(recorder.record.call_args.args[0].compiler_version, 'public-query-v4:experimental')
        self.assertEqual(recorder.record.call_args.args[0].plan, 'estimated scan')

class AccessTests(unittest.TestCase):
    def test_query_token_appends_and_public_cannot_read_history(self):
        app = FastAPI()
        app.add_middleware(ApiAccessMiddleware)
        app.include_router(router)
        client = TestClient(app)
        with patch.dict(os.environ, PERIPLUS_ADMIN_API_TOKEN='admin-test', PERIPLUS_PUBLIC_API_TOKEN='public-test', PERIPLUS_QUERY_API_TOKEN='query-test'):
            self.assertEqual(client.get('/query-history', headers={'Authorization':'Bearer public-test'}).status_code,403)
            self.assertEqual(client.get('/query-history', headers={'Authorization':'Bearer query-test'}).status_code,401)
            self.assertEqual(client.post('/internal/query-history', headers={'Authorization':'Bearer public-test'}, json={}).status_code,401)
            with patch('periplus.operations.api.query_history.store') as store:
                response = client.post('/internal/query-history', headers={'Authorization':'Bearer query-test'}, json=execution().model_dump(mode='json'))
                self.assertEqual(response.status_code,204)
                store.return_value.record.assert_called_once()
            self.assertEqual(client.post('/internal/query-history', headers={'Authorization':'Bearer query-test'}, content=b'x'*(1024*1024+1)).status_code,413)

@unittest.skipUnless(os.environ.get('QUERY_HISTORY_TEST_DATABASE_URL'), 'requires isolated Postgres test database')
class StoreTests(unittest.TestCase):
    def setUp(self):
        from postgres_fixture import isolated_database
        self.database = isolated_database(os.environ['QUERY_HISTORY_TEST_DATABASE_URL'])
        self.engine = self.database.__enter__()
        self.addCleanup(self.database.__exit__, None, None, None)
        with self.engine.begin() as connection:
            connection.execute(text('CREATE SCHEMA state'))
        QueryExecution.__table__.create(self.engine)
        self.sessions = sessionmaker(self.engine)
        self.store = QueryHistoryStore(self.sessions)

    def test_percentiles_filters_idempotence_and_detail(self):
        values = [execution(elapsed_ms=n, source='assistant') for n in [100,200,300]]
        for value in values:
            self.store.record(value)
        self.store.record(values[0])
        self.store.record(execution(elapsed_ms=1, outcome='rejected', error_code='service_busy'))
        self.store.record(execution(elapsed_ms=99999, source='admin'))
        self.store.record(execution(elapsed_ms=99999, operation='prepare'))
        dashboard = self.store.dashboard()
        self.assertEqual(dashboard.summary.executions,4)
        self.assertEqual(dashboard.summary.successes,3)
        self.assertEqual(dashboard.summary.p50_ms,200)
        self.assertEqual(dashboard.summary.p95_ms,290)
        self.assertEqual(dashboard.pattern_count,1)
        self.assertEqual(dashboard.patterns[0].failures,1)
        self.assertEqual(len(self.store.executions().executions),4)
        self.assertEqual(self.store.detail(values[0].execution_id).sql_text,'select 1')
        self.assertEqual(self.store.dashboard(source='admin').summary.p50_ms,99999)
        self.assertEqual(self.store.dashboard(operation='prepare').summary.executions,1)

    def test_plan_roundtrip_and_comparison(self):
        first = execution(plan='scan a', plan_fingerprint='a'*64, plan_truncated=False,
            diagnostics=[], engine_version='test', compiler_version='v1',
            effective_limits={'max_rows': 10, 'max_duration_seconds': 2, 'max_result_bytes': 1048576})
        self.store.record(first)
        self.store.record(execution(plan='scan a', plan_fingerprint='a'*64, engine_version='test',
            compiler_version='v1', outcome='timeout', elapsed_ms=2000))
        self.store.record(execution(plan='scan b', plan_fingerprint='b'*64, elapsed_ms=20))
        self.store.record(execution())
        self.store.record(execution(source='admin', plan_fingerprint='a'*64))
        self.assertEqual(self.store.detail(first.execution_id), first)
        self.assertEqual(self.store.dashboard().plans, [])
        plans = self.store.dashboard(pattern=first.query_fingerprint).plans
        self.assertEqual(len(plans), 3)
        a = next(p for p in plans if p.plan_fingerprint == 'a'*64)
        self.assertEqual((a.executions, a.successes, a.timeouts, a.p95_ms), (2, 1, 1, 100))
        self.assertIsNotNone(self.store.detail(a.example_execution_id))

    def test_usage_and_retention(self):
        self.store.record(execution(sql_text='select count(*) from web.observation', **shape('select count(*) from web.observation')))
        self.assertEqual(self.store.dashboard().relations[0].name,'web.observation')
        self.assertEqual(self.store.dashboard().functions[0].name,'count')
        expired = execution(started_at=datetime.now(UTC)-timedelta(days=31))
        with self.sessions.begin() as session:
            session.add(QueryExecution(**expired.model_dump()))
        self.assertIsNone(self.store.detail(expired.execution_id))
        self.assertEqual(self.store.dashboard().summary.executions,1)
        self.assertEqual(self.store.cleanup(),1)
