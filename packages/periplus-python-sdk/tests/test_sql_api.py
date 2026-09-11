import json
import os
import unittest
from unittest.mock import patch

import httpx
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from periplus_sdk import sql_api
from test_client import RESULT


class SQLApiTests(unittest.TestCase):
    def test_engine_options_environment_and_queries(self):
        factory = httpx.Client
        for mode in ('stable', 'experimental'):
            requests = []
            def handler(request):
                requests.append(request)
                return httpx.Response(200, json=dict(RESULT, columns=['n'], types=['INTEGER'], rows=[[42]], truncated=False))
            with self.subTest(mode=mode), patch.dict(os.environ, {'PERIPLUS_PUBLIC_URL':'https://public.example/prefix'}), patch(
                'periplus_sdk.client.httpx.Client',
                side_effect=lambda **kw: factory(**kw, transport=httpx.MockTransport(handler)),
            ):
                engine = sql_api.create_engine(mode=mode, timeout=37)
                self.assertIsInstance(engine, Engine)
                self.assertEqual(requests, [])
                try:
                    self.assertEqual(inspect(engine).default_schema_name, 'public_v1')
                    with engine.connect() as conn:
                        self.assertEqual(conn.connection.dbapi_connection._client._http.timeout.read,37)
                        self.assertEqual(conn.execute(text('SELECT :n AS n'), {'n':42}).fetchall(), [(42,)])
                    suffix = 'experimental/' if mode == 'experimental' else ''
                    self.assertEqual(requests[0].url.path, '/prefix/api/query/'+suffix+'exec')
                    self.assertEqual(json.loads(requests[0].content)['parameters'], [42])
                    self.assertEqual(json.loads(requests[0].content)['schema_version'], 'public_v1')
                finally:
                    engine.dispose()
