import json
import logging
import unittest
from unittest.mock import patch
from periplus.platform.telemetry import SafeFormatter
from periplus.platform.health import HealthMonitor
from periplus.materialization import metrics

class TelemetryTests(unittest.TestCase):
    def test_native_exception_and_arguments_never_render(self):
        try:
            raise ValueError('password=sentinel-secret SELECT private_payload')
        except ValueError:
            import sys
            record = logging.LogRecord('periplus.ingestion', logging.ERROR, __file__, 1,
                'ingestion_failed %s', ('sentinel-secret',), sys.exc_info())
        text = SafeFormatter('ingestor').format(record)
        self.assertNotIn('sentinel-secret', text)
        self.assertNotIn('private_payload', text)
        self.assertEqual(json.loads(text)['exception_type'], 'ValueError')

    def test_library_urls_and_headers_are_not_rendered(self):
        record = logging.LogRecord('httpx', logging.INFO, __file__, 1,
            'GET https://signed.invalid?token=sentinel-secret', (), None)
        self.assertNotIn('sentinel-secret', SafeFormatter('api').format(record))

    def test_dependency_failure_is_not_liveness_failure(self):
        monitor = HealthMonitor()
        monitor.dependencies_unavailable('secret-native-error')
        self.assertTrue(monitor.alive())
        self.assertFalse(monitor.status()[0])
        with patch('periplus.platform.health.time.monotonic', return_value=monitor._last_heartbeat + 10):
            self.assertFalse(monitor.alive())

    def test_replay_does_not_increment_committed_counts(self):
        before = metrics._output_rows._value.get()
        kwargs = dict(source_items=2, source_bytes=400, output_rows=10, output_bytes=200,
                      project_seconds=0, parquet_seconds=0, commit_seconds=0)
        metrics.batch(**kwargs, already_applied=True)
        self.assertEqual(metrics._output_rows._value.get(), before)
        metrics.batch(**kwargs, already_applied=False, superseded=True)
        self.assertEqual(metrics._output_rows._value.get(), before)
        metrics.batch(**kwargs, already_applied=False)
        self.assertEqual(metrics._output_rows._value.get(), before + 10)

    def test_http_metric_labels_and_logs_never_include_query_strings(self):
        import asyncio
        from periplus.platform.telemetry import HttpTelemetry
        from types import SimpleNamespace
        async def app(scope, receive, send):
            scope['route'] = SimpleNamespace(path='/items/{id}')
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        async def send(message): pass
        scope = {'type': 'http', 'path': '/items/sensitive-id', 'method': 'GET',
                 'query_string': b'sql=sentinel-secret', 'headers': []}
        with patch('periplus.platform.telemetry.event') as emit:
            asyncio.run(HttpTelemetry(app, 'test')(scope, None, send))
        self.assertEqual(emit.call_args.kwargs['route'], '/items/{id}')
        self.assertNotIn('sentinel-secret', str(emit.call_args))
        self.assertNotIn('sensitive-id', str(emit.call_args))

    def test_query_metrics_is_available_without_query_permission(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from periplus.query.server_http import QueryAccessMiddleware
        from periplus.operations.api.metrics import router
        app = FastAPI()
        app.include_router(router)
        app.add_middleware(QueryAccessMiddleware)
        with TestClient(app) as client:
            response = client.get('/metrics')
            self.assertEqual(response.status_code, 200)
            self.assertIn('periplus_http_requests', response.text)
            self.assertEqual(client.post('/query/exec', json={'sql': 'SELECT 1'}).status_code, 401)


    def test_frontier_cleanup_counts_survive_safe_formatting_without_payloads(self):
        import json
        import logging
        record = logging.LogRecord('periplus.operations.janitor', logging.INFO, __file__, 1,
                                   'frontier_cleanup', (), None)
        record.telemetry = dict(batches=70, collections_removed=1, acquisitions_removed=4300,
                                more=False, payload='sentinel-secret')
        data = json.loads(SafeFormatter('janitor').format(record))
        self.assertEqual(data['batches'], 70)
        self.assertEqual(data['acquisitions_removed'], 4300)
        self.assertEqual(data['collections_removed'], 1)
        self.assertFalse(data['more'])
        self.assertNotIn('payload', data)
        record.telemetry = dict(acquisitions_removed='sentinel-secret')
        self.assertNotIn('sentinel-secret', SafeFormatter('janitor').format(record))
