import json
import os
import unittest
from unittest.mock import patch

import httpx

from periplus_sdk import AsyncClient, Client, ApiError, ConfigurationError, ResponseError, TransportError

PREP = dict(schema_version='public_v1', query_id='q', sql='SELECT ? AS n', parameters=[1], diagnostics=[], plan='plan')
RESULT = dict(**PREP, columns=['n', 'n'], types=['BIGINT', 'DECIMAL(20,2)'],
              rows=[['9007199254740993', '123.45']], truncated=True, elapsed_ms=1.2, source_snapshot=4)


class ClientTests(unittest.TestCase):
    def client(self, handler):
        factory = httpx.Client
        with patch('periplus_sdk.client.httpx.Client', side_effect=lambda **kw:
                   factory(**kw, transport=httpx.MockTransport(handler))):
            client = Client('https://public.example/prefix/')
        self.addCleanup(client.close)
        return client

    def test_public_routes_values_and_no_credentials(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertNotIn('authorization', request.headers)
            self.assertEqual(request.headers['x-periplus-query-source'], 'sdk')
            if request.url.path.endswith('helpers'):
                return httpx.Response(200, json={'catalogue_version': '1.0.0', 'helpers': []})
            self.assertEqual(json.loads(request.content), {'sql': PREP['sql'], 'parameters': [1], 'schema_version': 'public_v1'})
            return httpx.Response(200, json=RESULT if request.url.path.endswith('exec') else PREP)
        with patch.dict(os.environ, {'PERIPLUS_QUERY_API_TOKEN': 'secret', 'PERIPLUS_API_TOKEN': 'admin'}):
            client = self.client(handler)
            self.assertEqual(client.prepare(PREP['sql'], [1]).plan, 'plan')
            result = client.execute(PREP['sql'], [1])
            self.assertEqual(result.rows, RESULT['rows'])
            self.assertEqual(result.columns, ['n', 'n'])
            self.assertTrue(result.truncated)
            self.assertEqual(result.source_snapshot, 4)
            self.assertEqual(client.helpers().catalogue_version, '1.0.0')
        self.assertEqual([r.url.path for r in requests], ['/prefix/api/query/prep', '/prefix/api/query/exec', '/prefix/api/query/helpers'])

    def test_errors_preserve_categories_and_never_retry(self):
        for status, body, code in [(429, {'code': 'service_busy', 'detail': 'Busy'}, 'service_busy'),
                                   (403, {'code': 'feature_disabled', 'detail': 'Disabled'}, 'feature_disabled'),
                                   (503, {'detail': {'code': 'access_unavailable', 'detail': 'Offline'}}, 'access_unavailable'),
                                   (422, {'detail': [{'msg': 'Invalid'}]}, None)]:
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(status, json=body, headers={'Retry-After': '2'})
            with self.subTest(status=status), self.client(handler) as client:
                with self.assertRaises(ApiError) as raised:
                    client.execute('SELECT 1')
                self.assertEqual(raised.exception.status_code, status)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.retry_after_seconds, 2)
                self.assertEqual(len(calls), 1)

    def test_malformed_response_redirect_and_transport_error(self):
        for response in [httpx.Response(200, text='<html>'), httpx.Response(200, json={})]:
            with self.client(lambda request: response) as client:
                with self.assertRaises(ResponseError):
                    client.execute('SELECT 1')
        with self.client(lambda request: httpx.Response(302, headers={'Location': 'https://other.example'})) as client:
            with self.assertRaises(ApiError):
                client.helpers()
        def fail(request):
            raise httpx.ReadTimeout('contains sensitive origin', request=request)
        with self.client(fail) as client:
            with self.assertRaisesRegex(TransportError, 'Could not complete'):
                client.execute('SELECT 1')

    def test_configuration_and_removed_privileged_api(self):
        for url in ['', 'postgres://host', 'https://user:secret@host', 'https://host/?token=secret', 'https://host/#a']:
            with patch.dict(os.environ, {}, clear=True), self.subTest(url=url):
                with self.assertRaises(ConfigurationError):
                    Client(url)
        for timeout in [0, -1, float('nan'), float('inf')]:
            with self.assertRaises(ConfigurationError):
                Client('https://public.example', timeout=timeout)
        with patch.dict(os.environ, {'PERIPLUS_PUBLIC_URL': 'https://public.example'}):
            with Client() as client:
                self.assertEqual(str(client._http.base_url), 'https://public.example/')
        import periplus_sdk
        self.assertFalse(hasattr(periplus_sdk, 'conn'))
        self.assertFalse(hasattr(periplus_sdk, 'collections'))
        self.assertFalse(hasattr(periplus_sdk, 'frontier'))


class AsyncClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_parity_and_close(self):
        factory = httpx.AsyncClient
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.path.endswith('helpers'):
                return httpx.Response(200, json={'catalogue_version': '1.0.0', 'helpers': []})
            return httpx.Response(200, json=RESULT if request.url.path.endswith('exec') else PREP)
        with patch('periplus_sdk.client.httpx.AsyncClient', side_effect=lambda **kw:
                   factory(**kw, transport=httpx.MockTransport(handler))):
            async with AsyncClient('https://public.example') as client:
                self.assertEqual((await client.prepare('SELECT ?', [1])).parameters, [1])
                self.assertEqual((await client.execute('SELECT ?', [1])).rows, RESULT['rows'])
                self.assertEqual((await client.helpers()).catalogue_version, '1.0.0')
        self.assertTrue(client._http.is_closed)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(r.headers['x-periplus-query-source'] == 'sdk' for r in calls))
