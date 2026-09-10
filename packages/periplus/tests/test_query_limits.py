"""Authoritative limits cannot be supplied by query callers or skipped on policy failure."""
import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError

from periplus.operations.access.schemas import AccessPolicy, QueryLimits
from periplus.operations.api.access import router as access_router
from periplus.platform.api_access import ApiAccessMiddleware
from periplus.query.limits import QueryLimitsClient, QueryLimitsUnavailable
from periplus.query.server_http import router
from periplus.query.service import PreparedQuery


class LimitsTests(unittest.IsolatedAsyncioTestCase):
    async def test_policy_reads_refresh_and_fail_closed(self):
        with patch.dict(os.environ, PERIPLUS_QUERY_API_TOKEN='query-secret', PERIPLUS_API_URL='http://control.test'):
            reader = QueryLimitsClient()
        await reader.close()
        responses = [httpx.Response(200, json={'sql': QueryLimits(max_rows=7).model_dump()}),
                     httpx.Response(200, json={'sql': QueryLimits(max_rows=3).model_dump()}),
                     httpx.Response(503, text='private origin'),
                     httpx.Response(200, json={'sql': {'max_rows': 3}}),
                     httpx.Response(200, json={'sql': QueryLimits().model_dump() | {'max_rows': 10001}})]
        def handler(request):
            self.assertEqual(request.url.path, '/access')
            self.assertEqual(request.headers['Authorization'], 'Bearer query-secret')
            return responses.pop(0)
        reader.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://control.test/',
                                         headers={'Authorization': 'Bearer query-secret'})
        try:
            self.assertEqual((await reader.read()).max_rows, 7)
            self.assertEqual((await reader.read()).max_rows, 3)
            for _ in range(3):
                with self.assertRaises(QueryLimitsUnavailable):
                    await reader.read()
        finally:
            await reader.close()

    async def test_http_refresh_bounds_and_failure_recording(self):
        app = FastAPI()
        app.include_router(router)
        app.state.query_slot = asyncio.Semaphore(1)
        app.state.query_history = SimpleNamespace(record=AsyncMock())
        reader = app.state.query_limits = SimpleNamespace(read=AsyncMock())
        captured = []
        def prepare(payload, *, limits, evidence):
            captured.append(limits)
            return PreparedQuery(query_id='00000000-0000-4000-8000-000000000001', sql=payload.sql,
                                 parameters=payload.parameters, diagnostics=[], plan='plan')
        app.state.query_service = SimpleNamespace(prepare=prepare)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://query.test') as client:
            for count in (7, 3):
                reader.read.return_value = QueryLimits(max_rows=count)
                self.assertEqual((await client.post('/query/prep', json={'sql': 'SELECT 1'})).status_code, 200)
            self.assertEqual([limits.max_rows for limits in captured], [7, 3])
            # Requests cannot raise any server-side limit.
            invalid = await client.post('/query/prep', json={'sql': 'SELECT 1', 'max_rows': 999999})
            self.assertEqual(invalid.status_code, 422)
            reader.read.side_effect = QueryLimitsUnavailable()
            response = await client.post('/query/prep', json={'sql': 'SELECT 1'})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()['code'], 'access_unavailable')
            self.assertEqual(len(captured), 2)
            self.assertEqual(app.state.query_history.record.call_args.args[0].error_code, 'access_unavailable')
            self.assertFalse(app.state.query_slot.locked())
            reader.read.reset_mock(side_effect=True)
            async with app.state.query_slot:
                self.assertEqual((await client.post('/query/prep', json={'sql': 'SELECT 1'})).status_code, 429)
            reader.read.assert_not_called()


class PolicyTests(unittest.TestCase):
    def test_supported_bounds(self):
        for field, value in [('max_rows', 0), ('max_rows', 10001), ('max_duration_seconds', 0),
                             ('max_duration_seconds', 121), ('max_result_bytes', 1024),
                             ('max_result_bytes', 64 * 1024 * 1024 + 1)]:
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                AccessPolicy.model_validate({'sql': {field: value}})

    def test_query_token_reads_settings_but_cannot_edit_or_read_history(self):
        app = FastAPI()
        app.add_middleware(ApiAccessMiddleware)
        app.include_router(access_router)
        app.state.frontier_sessions = Mock()
        policy = AccessPolicy().model_dump() | {'version': 1, 'crawl_admission': {'pending_acquisitions': 0, 'accepting': True}}
        with patch.dict(os.environ, PERIPLUS_ADMIN_API_TOKEN='admin', PERIPLUS_PUBLIC_API_TOKEN='public', PERIPLUS_QUERY_API_TOKEN='query'):
            with patch('periplus.operations.api.access.AccessStore.read', return_value=policy), TestClient(app) as client:
                headers = {'Authorization': 'Bearer query'}
                result = client.get('/access', headers=headers)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()['sql']['max_result_bytes'], 8 * 1024 * 1024)
                self.assertEqual(client.put('/access', headers=headers, json=policy).status_code, 401)
                self.assertEqual(client.get('/query-history', headers=headers).status_code, 401)
                self.assertEqual(client.get('/access', headers={'Authorization': 'Bearer wrong'}).status_code, 401)
