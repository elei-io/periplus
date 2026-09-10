from periplus.operations.access.schemas import QueryLimits
from unittest.mock import AsyncMock
"""SDK wire responses and history pass through the actual query HTTP adapter."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock

import httpx
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'periplus-python-sdk/src'))
from periplus_sdk import AsyncClient, ApiError
from periplus_sdk.types import QueryHelpers
from periplus.query.helpers import query_helpers
from periplus.query.server_http import router
from periplus.query.service import PreparedQuery, QueryResult
from periplus.query.validation import _bounded_query


class SdkQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_responses_rejections_and_sdk_history(self):
        app = FastAPI()
        app.include_router(router, prefix='/api')
        app.state.query_slot = asyncio.Semaphore(1)
        history = app.state.query_history = AsyncMock()
        def prepare(payload, *, limits, evidence):
            _bounded_query(payload.sql, max_rows=limits.max_rows)
            return PreparedQuery(query_id='00000000-0000-4000-8000-000000000001', sql=payload.sql, parameters=payload.parameters, diagnostics=[], plan='plan')
        def execute(payload, *, limits, evidence):
            return QueryResult(**prepare(payload, limits=limits, evidence=evidence).model_dump(), columns=['n'], types=['INTEGER'],
                               rows=[[1]], elapsed_ms=1, source_snapshot=7, truncated=False)
        app.state.query_limits = AsyncMock()
        app.state.query_limits.read.return_value = QueryLimits()
        app.state.query_service = Mock(prepare=prepare, execute=execute, compiler_version="public-query-v4:stable")
        # This fixture exercises the service adapter; the real Next gateway supplies authentication.
        async with AsyncClient('http://public.test') as client:
            await client._http.aclose()
            client._http = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://public.test/',
                                            headers={'x-periplus-query-source': 'sdk'})
            self.assertEqual((await client.prepare('SELECT ? AS n', [1])).parameters, [1])
            self.assertEqual((await client.execute('SELECT ? AS n', [1])).source_snapshot, 7)
            for sql in ['DELETE FROM web.observation', 'SELECT * FROM ingest.visits', "SELECT * FROM read_csv('/etc/passwd')"]:
                with self.assertRaises(ApiError) as raised:
                    await client.execute(sql)
                self.assertEqual(raised.exception.code, 'sql_invalid')
            self.assertEqual((await client.helpers()).model_dump(), QueryHelpers.model_validate(query_helpers().model_dump()).model_dump())
        records = [call.args[0] for call in history.record.await_args_list]
        self.assertEqual(len(records), 5)
        self.assertTrue(all(record.source == 'sdk' for record in records))
        self.assertEqual(records[1].parameters, [1])
        self.assertEqual(records[1].source_snapshot, 7)
        self.assertTrue(all(record.outcome == 'rejected' for record in records[2:]))
