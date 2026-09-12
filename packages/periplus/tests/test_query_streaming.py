"""HTTP stream ownership, terminal failures and authoritative input budgets."""
import asyncio
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from periplus.operations.access.schemas import QueryLimits
from periplus.query.server_http import QueryAccessMiddleware, router
from periplus.query.service import QueryResult


class StreamingTests(unittest.TestCase):
    def app(self, execute, limits=QueryLimits()):
        app = FastAPI()
        app.include_router(router)
        app.add_middleware(QueryAccessMiddleware)
        app.state.query_slot = asyncio.Semaphore(1)
        app.state.query_limits = SimpleNamespace(read=AsyncMock(return_value=limits))
        app.state.query_service = SimpleNamespace(execute=execute, compiler_version='public-query-v11:stable', connection=None)
        return app

    def test_frames_completion_and_slot_release(self):
        def execute(payload, *, emit, **kw):
            emit({'type': 'metadata', 'columns': ['n'], 'types': ['INTEGER']})
            emit({'type': 'rows', 'rows': [[1], [2]]})
            return QueryResult(query_id='00000000-0000-4000-8000-000000000001', sql=payload.sql,
                               parameters=[], diagnostics=[], plan='', columns=['n'], types=['INTEGER'],
                               rows=[], row_count=2, result_bytes=10, truncated=False, elapsed_ms=1, source_snapshot=4)
        app = self.app(execute)
        with patch.dict(os.environ, PERIPLUS_QUERY_API_TOKEN='test'), TestClient(app) as client:
            for _ in range(2):
                response = client.post('/query/exec', headers={'authorization': 'Bearer test', 'accept': 'application/x-ndjson'}, json={'sql': 'SELECT 1'})
                frames = [json.loads(line) for line in response.text.splitlines() if line]
                self.assertEqual([f['type'] for f in frames], ['metadata', 'rows', 'complete'])
                self.assertEqual(frames[-1]['row_count'], 2)
                self.assertFalse(app.state.query_slot.locked())

    def test_partial_failure_never_emits_completion(self):
        def execute(payload, *, emit, **kw):
            emit({'type': 'metadata'})
            emit({'type': 'rows', 'rows': [[1]]})
            raise TimeoutError('native secret')
        app = self.app(execute)
        with patch.dict(os.environ, PERIPLUS_QUERY_API_TOKEN='test'), TestClient(app) as client:
            response = client.post('/query/exec', headers={'authorization': 'Bearer test', 'accept': 'application/x-ndjson'}, json={'sql': 'SELECT 1'})
        frames = [json.loads(line) for line in response.text.splitlines() if line]
        self.assertEqual(frames[-1]['type'], 'error')
        self.assertNotIn('native secret', response.text)
        self.assertFalse(app.state.query_slot.locked())

    def test_large_inputs_and_operator_bounds(self):
        def execute(*args, **kwargs):
            raise AssertionError('SQL must not execute')
        app = self.app(execute, QueryLimits(max_request_bytes=1024, max_parameter_values=2))
        with patch.dict(os.environ, PERIPLUS_QUERY_API_TOKEN='test'), TestClient(app) as client:
            for accept in ('application/json', 'application/x-ndjson'):
                headers = {'authorization': 'Bearer test', 'accept': accept}
                response = client.post('/query/exec', headers=headers, json={'sql': 'SELECT ?', 'parameters': ['x'*200_000]})
                self.assertEqual(response.status_code, 413)
                self.assertEqual(response.json()['code'], 'request_limit')
                response = client.post('/query/exec', headers=headers, json={'sql': 'SELECT ?', 'parameters': [[1, 2]]})
                self.assertEqual(response.status_code, 413)
                self.assertEqual(response.json()['code'], 'parameter_limit')
                self.assertFalse(app.state.query_slot.locked())


class StreamOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_keeps_slot_until_backpressured_producer_stops(self):
        import threading
        from periplus.query.streaming import QueryStreamResponse
        from periplus.query.service import QueryRequest
        started = threading.Event()
        stopped = threading.Event()
        produced = []
        slot = asyncio.Semaphore(1)
        await slot.acquire()
        def execute(payload, *, emit, **kw):
            started.set()
            try:
                for i in range(10000):
                    emit({'type': 'rows', 'rows': [[i]]})
                    produced.append(i)
            finally:
                stopped.set()
        state = SimpleNamespace(query_slot=slot, query_service=SimpleNamespace(
            execute=execute, compiler_version='public-query-v11:stable', connection=None))
        request = SimpleNamespace(app=SimpleNamespace(state=state), state=SimpleNamespace(), headers={})
        response = QueryStreamResponse(request, QueryRequest(sql='SELECT 1'), QueryLimits())
        sending = asyncio.Event()
        async def send(message):
            if message['type'] == 'http.response.body':
                sending.set()
                await asyncio.Event().wait()
        async def receive():
            await asyncio.Event().wait()
        task = asyncio.create_task(response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send))
        await asyncio.wait_for(sending.wait(), 2)
        self.assertTrue(started.is_set())
        self.assertTrue(slot.locked())
        # One frame in send, two in the bounded producer queue.
        self.assertLessEqual(len(produced), 3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        self.assertTrue(stopped.is_set())
        self.assertFalse(slot.locked())
