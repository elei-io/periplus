"""Native query HTTP contract; transport and real-server checks live alongside it.

The former DuckLake fixture's optimizer passes, two query modes and pinned lake
snapshots are retired. Native SQL and SDK execution are covered by the opt-in
ClickHouse integration suite, independently of these HTTP boundary checks.
"""
import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from periplus.operations.access.schemas import QueryLimits
from periplus.platform.clickhouse import ClickHouseConfig, ClickHouseError
from periplus.query.models import QueryRequest
from periplus.query.server_http import router, QueryAccessMiddleware
from periplus.query.service import QueryService, ResultLimitError


class QueryServiceTests(unittest.TestCase):
    def setUp(self):
        self.database = Mock()
        self.rows = [[42]]
        def execute(sql, **kwargs):
            if sql.startswith('EXPLAIN'):
                return b'Expression'
            return json.dumps({'meta': [{'name': 'answer', 'type': 'UInt64'}], 'data': self.rows}).encode()
        self.database.execute.side_effect = execute
        with patch('periplus.query.service.ClickHouseClient', return_value=self.database):
            self.service = QueryService(ClickHouseConfig(url='http://localhost:8123',
                username='reader', password='test', query_only=True))
        self.addCleanup(self.service.close)
        self.app = FastAPI()
        self.app.state.query_service = self.service
        self.app.state.query_limits = AsyncMock()
        self.app.state.query_limits.read.return_value = QueryLimits()
        self.app.state.query_slot = asyncio.Semaphore(1)
        self.app.include_router(router)
        self.app.add_middleware(QueryAccessMiddleware)
        environment = patch.dict(os.environ, {'PERIPLUS_QUERY_API_TOKEN': 'query-test'})
        environment.start()
        self.addCleanup(environment.stop)
        self.http = TestClient(self.app)
        self.addCleanup(self.http.close)
        self.headers = {'Authorization': 'Bearer query-test'}

    def test_auth_routes_and_installed_capabilities(self):
        for endpoint in ('prep', 'exec'):
            self.assertEqual(self.http.post('/query/'+endpoint, json={'sql':'SELECT 42'}).status_code, 401)
            response = self.http.post('/query/'+endpoint, headers=self.headers, json={'sql':'SELECT 42'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.http.post('/query/'+endpoint, headers=self.headers,
                json={'sql':'DELETE FROM public_v1.capture'}).status_code, 422)
        self.assertEqual(self.http.get('/query/helpers').status_code, 401)
        helpers = self.http.get('/query/helpers', headers=self.headers).json()
        self.assertEqual(helpers['helpers'], [])
        self.assertEqual({item['name'] for item in helpers['relations']},
            {'public_v1.capture','public_v1.page','public_v1.html_element','public_v1.link'})
        for path in ('/query/helpers','/query/report','/graph-runs/'):
            self.assertEqual(self.http.post(path, headers=self.headers).status_code, 404)
        self.assertEqual(self.http.post('/query/exec', headers=self.headers,
            content='x'*(16*1024*1024+1)).status_code, 413)

    def test_server_failure_is_safe_and_releases_http_admission(self):
        for code, status in (('47',422), ('159',408), ('transport',503)):
            with patch.object(self.service, 'execute', side_effect=ClickHouseError('private-id', code=code)):
                response = self.http.post('/query/exec', headers=self.headers, json={'sql':'SELECT 42'})
            self.assertEqual(response.status_code, status, response.text)
            self.assertNotIn('private-id', response.text)
            self.assertFalse(self.app.state.query_slot.locked())
        self.assertEqual(self.http.post('/query/exec', headers=self.headers,
            json={'sql':'SELECT 42'}).json()['rows'], [[42]])

    def test_operator_row_limits_are_read_for_each_operation(self):
        self.rows = [[i] for i in range(8)]
        for count in (2,7):
            self.app.state.query_limits.read.return_value = QueryLimits(max_rows=count)
            result = self.http.post('/query/exec', headers=self.headers, json={'sql':'SELECT 42'}).json()
            self.assertEqual(len(result['rows']), count)
            self.assertTrue(result['truncated'])
        self.assertEqual(self.app.state.query_limits.read.await_count, 2)

    def test_metadata_budget_failure_emits_no_partial_stream(self):
        self.rows = []
        frames = []
        with self.assertRaises(ResultLimitError):
            self.service.execute(QueryRequest(sql='SELECT ? WHERE false', parameters=['x'*(2*1024*1024)]),
                limits=QueryLimits(max_result_bytes=1024*1024), emit=frames.append)
        self.assertEqual(frames, [])
        self.assertFalse(self.service._lock.locked())
