"""Opt-in query-account isolation against the disposable Compose ClickHouse."""
import os
import unittest
from uuid import uuid4

from periplus.platform.clickhouse import ClickHouseClient, ClickHouseConfig, ClickHouseError
from periplus.query.models import QueryRequest
from periplus.query.service import QueryService


@unittest.skipUnless(os.environ.get('PERIPLUS_TEST_CLICKHOUSE') == '1', 'requires disposable ClickHouse')
class ClickHouseQueryIntegrationTests(unittest.TestCase):
    def test_server_enforces_public_only_readonly_and_resource_constraints(self):
        client = ClickHouseClient(ClickHouseConfig.for_query())
        self.addCleanup(client.close)
        self.assertEqual(client.query('SELECT 42 AS answer')['data'], [{'answer': 42}])
        client.execute('SELECT capture_id FROM public_v1.capture LIMIT 1')
        table = 'forbidden_'+uuid4().hex
        for sql in (
            'SELECT * FROM ingest.visits LIMIT 0',
            'SELECT * FROM material.visit_results LIMIT 0',
            f'CREATE TABLE public_v1.{table} (n UInt64) ENGINE=Memory',
            'SELECT 1 SETTINGS max_memory_usage=1073741824',
            'SELECT 1 SETTINGS max_threads=8',
            'SELECT 1 SETTINGS max_execution_time=60',
        ):
            with self.subTest(sql=sql), self.assertRaises(ClickHouseError) as rejected:
                client.execute(sql)
            self.assertIn(rejected.exception.code, {'164', '497', '452'})
        self.assertEqual(client.query('SELECT 42 AS answer')['data'], [{'answer': 42}])

    def test_native_preparation_buffered_and_streamed_results_preserve_exact_values(self):
        service = QueryService(ClickHouseConfig.for_query())
        self.addCleanup(service.close)
        payload = QueryRequest(sql='SELECT ? AS n, ? AS text', parameters=[9007199254740993, '雪'])
        prepared = service.prepare(payload)
        self.assertTrue(prepared.plan)
        result = service.execute(payload)
        self.assertEqual(result.rows, [['9007199254740993', '雪']])
        self.assertIsNone(result.source_snapshot)
        frames = []
        streamed = service.execute(payload, emit=frames.append)
        rows = [row for frame in frames if frame['type']=='rows' for row in frame['rows']]
        self.assertEqual(rows, result.rows)
        self.assertEqual(streamed.rows, result.rows)

    def test_python_sdk_accepts_buffered_and_streamed_native_http_results(self):
        import asyncio
        import sys
        from pathlib import Path
        from unittest.mock import AsyncMock
        import httpx
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from periplus.operations.access.schemas import QueryLimits
        from periplus.query.server_http import router
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'periplus-python-sdk/src'))
        from periplus_sdk import Client

        service = QueryService(ClickHouseConfig.for_query())
        self.addCleanup(service.close)
        app = FastAPI()
        app.include_router(router, prefix='/api')
        app.state.query_service = service
        app.state.query_slot = asyncio.Semaphore(1)
        app.state.query_limits = AsyncMock()
        app.state.query_limits.read.return_value = QueryLimits()
        app.state.query_history = AsyncMock()
        with TestClient(app) as http, Client('http://query.test') as sdk:
            sdk._http.close()

            def transport(request):
                response = http.post(request.url.path, content=request.content, headers=dict(request.headers))
                return httpx.Response(response.status_code, headers=response.headers, content=response.content)

            sdk._http = httpx.Client(base_url='http://query.test', transport=httpx.MockTransport(transport))
            result = sdk.execute('SELECT ? AS n', [9007199254740993])
            self.assertEqual(result.rows, [['9007199254740993']])
            self.assertIsNone(result.source_snapshot)
            with sdk.stream('SELECT ? AS n', [9007199254740993]) as stream:
                self.assertEqual([row for batch in stream for row in batch], [['9007199254740993']])
                self.assertTrue(stream.result.complete)
                self.assertIsNone(stream.result.source_snapshot)

    def test_active_cpu_query_cancellation_closes_server_work_and_allows_reuse(self):
        import threading
        import time
        from concurrent.futures import ThreadPoolExecutor
        from periplus.platform.clickhouse import connect_clickhouse
        reader = ClickHouseClient(ClickHouseConfig.for_query())
        observer = connect_clickhouse()
        self.addCleanup(reader.close)
        self.addCleanup(observer.close)
        cancelled = threading.Event()
        identity = str(uuid4())

        def present():
            return observer.query('SELECT count() AS n FROM system.processes WHERE query_id={id: String}',
                                  parameters={'id': identity})['data'][0]['n']

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(reader.execute,
                'SELECT sum(cityHash64(SHA256(toString(number)))) FROM numbers(10000000)',
                query_id=identity, cancelled=cancelled)
            try:
                deadline = time.monotonic()+5
                while not present() and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertTrue(present())
                self.assertFalse(future.done())
                cancelled.set()
                reader.interrupt_query()
                with self.assertRaises(TimeoutError):
                    future.result(timeout=2)
                self.assertTrue(future.done(), 'The request itself must terminate, not just our observation timeout')
                deadline = time.monotonic()+2
                while present() and time.monotonic()<deadline:
                    time.sleep(.02)
                self.assertFalse(present())
            finally:
                cancelled.set()
                reader.interrupt_query()
        self.assertEqual(reader.query('SELECT 42 AS n')['data'], [{'n': 42}])
