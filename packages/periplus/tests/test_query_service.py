from periplus.operations.access.schemas import QueryLimits
from unittest.mock import AsyncMock
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import duckdb

from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.connection import DuckLakeConnectionFactory, _literal
from periplus.platform.catalogue.public import known_public_objects
from periplus.platform.catalogue.schema import expected_columns
from periplus.platform.catalogue.client import _column_type
from periplus.query.models import QueryRequest
from periplus.query.service import QueryService, BusyError


class QueryServiceTests(unittest.TestCase):
    def test_experimental_exact_text_rewrite_and_prepare_contract(self):
        from periplus.query.models import QueryMode
        from periplus.query.optimizations.element_text import ELEMENT_TEXT
        experimental = QueryService(self.config, mode=QueryMode.EXPERIMENTAL)
        self.addCleanup(experimental.close)
        request = QueryRequest(sql="SELECT content_id,node_index,text FROM html_element WHERE text='robot robot robotics careers' ORDER BY content_id,node_index")
        expected = self.service.execute(request)
        self.assertTrue(expected.rows)
        with patch('periplus.query.optimizations.element_text.lookup_candidates') as lookup:
            prepared = experimental.prepare(request)
            lookup.assert_not_called()
        self.assertIn('element_text_index_candidates.deferred.candidate_lookup', [d.code for d in prepared.diagnostics])
        actual = experimental.execute(request)
        self.assertEqual((actual.columns, actual.types, actual.rows), (expected.columns, expected.types, expected.rows))
        self.assertEqual(actual.sql, request.sql)
        self.assertEqual(actual.optimizations, [ELEMENT_TEXT.name])
        self.assertEqual(expected.optimizations, [])
        frames = []
        streamed = experimental.execute(request, emit=frames.append)
        self.assertEqual(frames[0]['optimizations'], [ELEMENT_TEXT.name])
        self.assertEqual([row for frame in frames if frame['type']=='rows' for row in frame['rows']], expected.rows)
        self.assertFalse(streamed.truncated)
        bounded = experimental.execute(QueryRequest(sql='SELECT content_id FROM html_element WHERE text=?', parameters=['robot robot robotics careers']))
        self.assertEqual(bounded.optimizations, [])

    def test_capture_link_pass_uses_service_snapshot_and_stream_limits(self):
        from periplus.query.models import QueryMode
        from periplus.query.optimizations.capture_links import lookup_keys

        self.service.close()
        writer = DuckLakeConnectionFactory(self.config).connect()
        writer.execute(
            "INSERT INTO periplus.material.link_occurrences (visit_id,element_index,source_url,target_url,raw_href) SELECT visit_id, i::INTEGER, requested_url, 'https://target/' || i, '/target' FROM periplus.ingest.visits CROSS JOIN range(3) n(i) WHERE requested_url='https://example.com/1'"
        )
        writer.close()
        self.service = QueryService(self.config)
        self.addCleanup(self.service.close)
        experimental = QueryService(self.config, mode=QueryMode.EXPERIMENTAL)
        self.addCleanup(experimental.close)
        request = QueryRequest(
            sql="""WITH selected AS (
            SELECT capture_id FROM capture WHERE page_url='https://example.com/1'
            ORDER BY captured_at DESC,capture_id DESC LIMIT 1)
            SELECT l.target_url,count(*) AS occurrences FROM link l JOIN selected s
            ON l.capture_id=s.capture_id GROUP BY l.target_url ORDER BY l.target_url"""
        )
        expected = self.service.execute(request)
        self.assertEqual(expected.row_count, 3)
        with patch("periplus.query.optimizations.capture_links.lookup_keys") as lookup:
            prepared = experimental.prepare(request)
            lookup.assert_not_called()
        self.assertIn(
            "capture_link_scope.deferred.capture_lookup",
            [d.code for d in prepared.diagnostics],
        )
        snapshots = []

        def observe(context, matched):
            snapshots.append(
                context.connection.execute(
                    "SELECT id FROM ducklake_current_snapshot('periplus')"
                ).fetchone()[0]
            )
            return lookup_keys(context, matched)

        with patch(
            "periplus.query.optimizations.capture_links.lookup_keys",
            side_effect=observe,
        ):
            actual = experimental.execute(request)
        self.assertEqual(snapshots, [actual.source_snapshot])
        self.assertEqual(
            (actual.columns, actual.types, actual.rows),
            (expected.columns, expected.types, expected.rows),
        )
        self.assertEqual(actual.optimizations, ["capture_link_scope"])
        self.assertEqual(actual.sql, request.sql)
        frames = []
        limited = experimental.execute(
            request, limits=QueryLimits(max_rows=1), emit=frames.append
        )
        self.assertEqual(limited.optimizations, ["capture_link_scope"])
        self.assertTrue(limited.truncated)
        self.assertEqual(
            [row for f in frames if f["type"] == "rows" for row in f["rows"]],
            expected.rows[:1],
        )

    def test_passes_share_snapshot_deadline_and_cleanup(self):
        from periplus.query.optimizations.base import OptimizationPass, PassDecision
        snapshots = []
        def observe(context):
            snapshots.append(context.connection.execute(
                "SELECT id FROM ducklake_current_snapshot('periplus')").fetchone()[0])
            return PassDecision('not_applicable', 'probe', 'Snapshot probe completed.')
        service = QueryService(self.config, passes=(OptimizationPass('probe', observe),))
        self.addCleanup(service.close)
        result = service.execute(QueryRequest(sql='SELECT 42'))
        self.assertEqual(snapshots, [result.source_snapshot])
        self.assertEqual(result.rows, [[42]])

        def fail(context):
            raise RuntimeError('pass failure')
        service.passes = (OptimizationPass('broken', fail),)
        with self.assertRaisesRegex(RuntimeError, 'pass failure'):
            service.execute(QueryRequest(sql='SELECT 42'))
        service.passes = ()
        self.assertEqual(service.execute(QueryRequest(sql='SELECT 42')).rows, [[42]])

        def slow(context):
            context.connection.execute('SELECT sum(i) FROM range(10000000000) t(i)').fetchall()
            return PassDecision('not_applicable', 'slow', 'Slow probe completed.')
        service.passes = (OptimizationPass('slow', slow),)
        service.deadline = 0.05
        with self.assertRaises(TimeoutError):
            service.execute(QueryRequest(sql='SELECT 42'))
        service.passes = ()
        service.deadline = None
        self.assertEqual(service.execute(QueryRequest(sql='SELECT 42')).rows, [[42]])

    def test_endpoint_schema_isolation(self):
        from periplus.query.models import QueryMode
        from periplus.query.helpers import query_helpers
        experimental = QueryService(self.config, mode=QueryMode.EXPERIMENTAL)
        self.addCleanup(experimental.close)
        for service, schema, other in ((self.service, "public_v1", "experimental"), (experimental, "experimental", "public_v1")):
            for execute in (service.prepare, service.execute):
                result = execute(QueryRequest(sql="SELECT * FROM search(['robot'])"))
                self.assertEqual(result.schema_version, schema)
                for sql in (f"SELECT * FROM {other}.capture", f"SELECT * FROM {other}.search(['robot'])",
                            f"DESCRIBE {other}.html_element", f"SHOW TABLES FROM {other}",
                            f"EXPLAIN SELECT * FROM {other}.capture"):
                    with self.subTest(schema=schema, sql=sql), self.assertRaises(ValueError):
                        execute(QueryRequest(sql=sql))
                with self.assertRaises(ValueError):
                    execute(QueryRequest(sql="SELECT 1", schema_version=other))
            self.assertEqual([item.name for item in query_helpers(schema).helpers], [f"{schema}.search"])
            self.assertTrue(all(item.name.startswith(schema + ".") for item in query_helpers(schema).relations))

    def test_removed_surfaces_in_both_modes(self):
        from periplus.query.models import QueryMode
        for mode in QueryMode:
            service = QueryService(self.config, mode=mode)
            self.addCleanup(service.close)
            for sql in ["SELECT * FROM search('robot')", 'SELECT * FROM html_node', 'SELECT * FROM term']:
                with self.assertRaises((ValueError, duckdb.Error)):
                    service.execute(QueryRequest(sql=sql))

    def test_stream_metadata_is_also_subject_to_result_byte_budget(self):
        from periplus.query.service import ResultLimitError
        frames = []
        with self.assertRaises(ResultLimitError):
            self.service.execute(QueryRequest(sql='SELECT ? WHERE false', parameters=['x' * (2 * 1024 * 1024)]),
                                 limits=QueryLimits(max_result_bytes=1024 * 1024), emit=frames.append)
        self.assertEqual(frames, [])

    def test_sdk_stream_contract_through_real_query_http(self):
        import asyncio
        import sys
        import httpx
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from periplus.query.server_http import router
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'periplus-python-sdk/src'))
        from periplus_sdk import Client, ApiError
        app = FastAPI()
        app.include_router(router, prefix='/api')
        app.state.query_service = self.service
        app.state.query_slot = asyncio.Semaphore(1)
        app.state.query_limits = AsyncMock()
        app.state.query_limits.read.return_value = QueryLimits(max_rows=25_000)
        app.state.query_history = AsyncMock()
        with TestClient(app) as http, Client('http://query.test') as sdk:
            sdk._http.close()
            def transport(request):
                response = http.post(request.url.path, content=request.content, headers=dict(request.headers))
                return httpx.Response(response.status_code, headers=response.headers, content=response.content)
            sdk._http = httpx.Client(base_url='http://query.test', transport=httpx.MockTransport(transport),
                                     headers={'x-periplus-query-source': 'sdk'})
            with sdk.stream('SELECT unnest(?::INTEGER[]) AS n', [list(range(20_001))]) as stream:
                rows = [row for batch in stream for row in batch]
                self.assertEqual(rows, [[i] for i in range(20_001)])
                self.assertTrue(stream.result.complete)
                self.assertEqual(stream.result.row_count, 20_001)
                self.assertEqual(stream.result.limits['max_rows'], 25_000)
            record = app.state.query_history.record.await_args_list[-1].args[0]
            self.assertEqual(record.result_rows, 20_001)
            self.assertEqual(record.source, 'sdk')
            app.state.query_limits.read.return_value = QueryLimits(max_rows=2)
            with sdk.stream('SELECT unnest(?::INTEGER[]) AS n', [[1, 2, 3]]) as stream:
                with self.assertRaises(ApiError) as raised:
                    list(stream)
                self.assertEqual(raised.exception.code, 'result_limit')
                self.assertTrue(stream.result.truncated)
            with sdk.stream('SELECT 7 AS n') as stream:
                self.assertEqual(list(stream), [[[7]]])

    def test_stream_batches_match_buffered_results_and_larger_limits(self):
        payload = QueryRequest(sql="SELECT unnest(?::INTEGER[]) AS n", parameters=[list(range(20_001))])
        frames = []
        limits = QueryLimits(max_rows=25_000)
        streamed = self.service.execute(payload, limits=limits, emit=frames.append)
        buffered = self.service.execute(payload, limits=limits)
        self.assertEqual(streamed.rows, [])
        self.assertEqual(streamed.row_count, 20_001)
        self.assertFalse(streamed.truncated)
        self.assertEqual(frames[0]['type'], 'metadata')
        self.assertEqual(frames[0]['source_snapshot'], buffered.source_snapshot)
        batches = [frame['rows'] for frame in frames[1:]]
        self.assertTrue(all(len(batch) <= 1024 for batch in batches))
        self.assertEqual([row for batch in batches for row in batch], buffered.rows)
        capped = self.service.execute(payload, limits=QueryLimits(max_rows=12), emit=lambda frame: None)
        self.assertEqual((capped.row_count, capped.truncation_reason), (12, 'max_rows'))
        self.assertTrue(capped.truncated)

    def test_stream_consumer_failure_releases_connection(self):
        def broken(frame):
            raise TimeoutError('consumer disconnected')
        with self.assertRaises(TimeoutError):
            self.service.execute(QueryRequest(sql='SELECT 1'), emit=broken)
        self.assertEqual(self.service.execute(QueryRequest(sql='SELECT 2')).rows, [[2]])


    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.config = CatalogueConfig(
            "periplus",
            str(root / "source.duckdb"),
            str(root / "data"),
            "ducklake",
        )
        d = duckdb.connect()
        d.execute("LOAD ducklake")
        d.execute(
            f"ATTACH {_literal('ducklake:' + self.config.metadata_path)} AS periplus (DATA_PATH {_literal(self.config.data_path)}, METADATA_SCHEMA 'ducklake')"
        )
        d.execute("USE periplus")
        for schema in ("ingest", "material", "public_v1", "experimental"):
            d.execute(f"CREATE SCHEMA {schema}")
        for relation, columns in expected_columns().items():
            definitions = ", ".join(
                f'"{name}" {_column_type(column)}' for name, column in columns.items()
            )
            d.execute(f"CREATE TABLE {relation.qualified} ({definitions})")
        base = files("periplus.platform.catalogue").joinpath("sql")
        from periplus.platform.catalogue.public_registry import INTERNAL_OBJECTS
        for item in (*INTERNAL_OBJECTS, *known_public_objects()):
            d.execute(base.joinpath(item.schema, item.resource).read_text())
        d.execute(
            "INSERT INTO ingest.visits (visit_id, requested_url, outcome) SELECT uuid(), 'https://example.com/' || i, 'success' FROM range(20) t(i)"
        )
        d.execute(
            "INSERT INTO ingest.visits (visit_id, requested_url, outcome) VALUES (uuid(), 'https://example.com/inline', 'success')"
        )
        d.execute("UPDATE ingest.visits SET document_id = '00000000-0000-0000-0000-000000000001' WHERE requested_url = 'https://example.com/inline'")
        d.execute("INSERT INTO ingest.documents (document_id, visit_id, detected_media_type, content_sha256) SELECT document_id, visit_id, 'text/html', 'helper-fixture' FROM ingest.visits WHERE document_id IS NOT NULL")
        d.execute("UPDATE ingest.visits SET document_id = uuid() WHERE document_id IS NULL")
        d.execute("INSERT INTO ingest.documents (document_id, visit_id, detected_media_type, content_sha256) SELECT document_id, visit_id, 'text/html', visit_id::VARCHAR FROM ingest.visits WHERE requested_url <> 'https://example.com/inline'")
        from element_fixture import seed
        seed(d, '<title>start</title><p>robot robot robotics careers</p>', 'helper-fixture', terms=True)
        d.close()
        self.service = QueryService(self.config)
        self.addCleanup(self.service.close)



    def test_search_and_graph_primitives_on_both_read_only_services(self):
        from periplus.query.models import QueryMode
        from periplus.query.helpers import query_helpers
        experimental = QueryService(self.config, mode=QueryMode.EXPERIMENTAL)
        self.addCleanup(experimental.close)
        sql = """SELECT p.url, c.content_id, s.score
            FROM search(?) s JOIN capture c USING (content_id)
            JOIN page p ON p.url=c.page_url ORDER BY p.url"""
        for service in (self.service, experimental):
            with self.subTest(schema=service.schema):
                payload = QueryRequest(sql=sql, parameters=[['robot', 'careers']])
                prepared = service.prepare(payload)
                result = service.execute(payload)
                self.assertEqual(result.rows, [['https://example.com/inline', 'helper-fixture', 2.0]])
                self.assertEqual(result.types, ['VARCHAR', 'VARCHAR', 'DOUBLE'])
                self.assertEqual(prepared.schema_version, service.schema)
                self.assertEqual(result.schema_version, service.schema)
                metadata = query_helpers(service.schema)
                self.assertEqual([h.name for h in metadata.helpers], [service.schema + '.search'])
                self.assertEqual({r.name.split('.')[-1] for r in metadata.relations},
                    {'page', 'capture', 'link', 'html_element', 'html_metadata', 'html_jsonld'})
                for removed in ('html_term', 'html_heading', 'material.html_terms'):
                    with self.assertRaises(ValueError):
                        service.execute(QueryRequest(sql=f'SELECT * FROM {removed}'))

    def test_modes_preserve_results_and_have_separate_admission(self):
        from periplus.query.models import QueryMode
        from periplus.operations.query_history.schemas import PreparationEvidence
        experimental = QueryService(self.config, mode=QueryMode.EXPERIMENTAL)
        self.addCleanup(experimental.close)
        payload = QueryRequest(sql="SELECT ? AS value", parameters=[42])
        stable = self.service.execute(payload)
        evidence = PreparationEvidence()
        with self.service._lock:
            result = experimental.execute(payload, evidence=evidence)
        self.assertEqual(result.rows, stable.rows)
        self.assertEqual(result.sql, payload.sql)
        self.assertEqual(result.parameters, payload.parameters)
        self.assertEqual(stable.query_mode, QueryMode.STABLE)
        self.assertEqual(result.query_mode, QueryMode.EXPERIMENTAL)
        self.assertEqual(result.optimizations, [])
        self.assertEqual(evidence.compiler_version, "public-query-v17:experimental")
        self.assertNotEqual(result.compiler_version, stable.compiler_version)
        with self.assertRaises(ValueError):
            QueryService(self.config, mode="invalid")



    def test_unmodified_preparation_execution_and_reuse(self):
        from periplus.operations.query_history.schemas import PreparationEvidence
        request = QueryRequest(sql="""SELECT c.page_url AS url, m.value AS title
            FROM html_element p JOIN capture c USING (content_id)
            JOIN html_metadata m USING (content_id)
            WHERE p.tag = 'p' AND p.text ILIKE ? AND m.name = ?""", parameters=['%robot%', 'title'])
        expected = self.service.connection.execute(request.sql,request.parameters).fetchall()
        evidence = PreparationEvidence()
        prepared = self.service.prepare(request, evidence=evidence)
        self.assertEqual(prepared.sql, request.sql)
        self.assertNotIn('content_scope', [d.code for d in prepared.diagnostics])
        self.assertEqual(evidence.plan, prepared.plan)
        self.assertEqual(evidence.diagnostics, [d.model_dump() for d in prepared.diagnostics])
        result = self.service.execute(request)
        self.assertEqual(result.sql, prepared.sql)
        self.assertEqual(result.rows, [list(row) for row in expected])
        self.assertEqual(result.rows, [['https://example.com/inline', 'start']])
        self.assertEqual(result.columns, ['url', 'title'])
        self.assertEqual(result.parameters, request.parameters)
        reused = self.service.execute(QueryRequest(sql=prepared.sql, parameters=prepared.parameters))
        self.assertEqual(reused.rows, result.rows)
        self.assertEqual(reused.sql, prepared.sql)
        # Invalid SQL is still rejected by native binding.
        with self.assertRaises(duckdb.BinderException):
            self.service.prepare(QueryRequest(sql=request.sql.replace('m.value', 'm.missing'), parameters=request.parameters))

    def test_joined_search_stays_unmodified_in_prepare_and_execute(self):
        request = QueryRequest(sql="""SELECT c.page_url AS url, m.value AS title
            FROM html_metadata m JOIN capture c USING (content_id)
            JOIN html_element p USING (content_id) WHERE p.tag = 'p' AND p.text ILIKE ? AND m.name = ?""",
            parameters=['%robot%', 'title'])
        prepared = self.service.prepare(request)
        result = self.service.execute(request)
        for response in (prepared, result):
            self.assertEqual(response.sql, request.sql)
            self.assertNotIn('content_scope', [d.code for d in response.diagnostics])
        self.assertEqual(prepared.sql, result.sql)
        self.assertEqual(result.rows, [['https://example.com/inline', 'start']])
        self.assertEqual(result.columns, ['url', 'title'])
        self.assertEqual(result.parameters, request.parameters)

    def test_compound_join_stays_unmodified_and_preserves_parameter_positions(self):
        request = QueryRequest(sql="""SELECT ? AS marker, c.page_url AS url, m.value AS title
            FROM html_element p JOIN capture c USING (content_id)
            JOIN html_metadata m ON (m.name = ? AND (m.content_id = c.content_id))
            WHERE p.tag = 'p' AND p.text ILIKE ? ORDER BY title""", parameters=['marker', 'title', '%robot%'])
        expected = self.service.connection.execute(request.sql,request.parameters).fetchall()
        prepared = self.service.prepare(request)
        result = self.service.execute(request)
        for response in (prepared, result):
            self.assertEqual(response.sql, request.sql)
            self.assertNotIn('content_scope', [d.code for d in response.diagnostics])
        self.assertEqual(prepared.sql, result.sql)
        self.assertEqual(result.rows, [list(row) for row in expected])
        self.assertEqual(result.rows, [['marker', 'https://example.com/inline', 'start']])
        reused = self.service.execute(QueryRequest(sql=prepared.sql, parameters=prepared.parameters))
        self.assertEqual(reused.rows, result.rows)



    def test_anonymous_parameter_cast_matches_duckdb_without_rewriting_sql(self):
        sql = "SELECT ?::INTEGER AS value, '?::UUID' AS marker /* ?:: is literal comment text */"
        request = QueryRequest(sql=sql, parameters=[7])
        prepared = self.service.prepare(request)
        result = self.service.execute(request)
        reference = self.service.execute(QueryRequest(
            sql="SELECT CAST(? AS INTEGER) AS value, '?::UUID' AS marker", parameters=[7]))
        self.assertEqual(prepared.sql, sql)
        self.assertEqual(result.sql, sql)
        self.assertEqual(result.rows, reference.rows)
        self.assertEqual(result.types, reference.types)
        self.assertEqual(result.rows, [[7, '?::UUID']])
        observation = self.service.execute(QueryRequest(sql="SELECT capture_id FROM public_v1.capture LIMIT 1")).rows[0][0]
        found = self.service.execute(QueryRequest(sql="SELECT count(*) AS n FROM public_v1.capture WHERE capture_id = ?::UUID", parameters=[observation]))
        self.assertEqual(found.rows, [[1]])
        for forbidden in ("SELECT ?::INTEGER; SELECT 2", "SELECT ?::INTEGER FROM ingest.visits"):
            with self.subTest(sql=forbidden), self.assertRaises(ValueError):
                self.service.prepare(QueryRequest(sql=forbidden, parameters=[7]))

    def test_preparation_evidence_and_failed_execution(self):
        from periplus.operations.query_history.schemas import PreparationEvidence
        evidence = PreparationEvidence()
        result = self.service.execute(QueryRequest(sql='SELECT 42'), evidence=evidence,
                                      limits=QueryLimits(max_rows=12))
        self.assertEqual(evidence.plan, result.plan)
        self.assertFalse(evidence.plan_truncated)
        self.assertEqual(len(evidence.plan_fingerprint), 64)
        self.assertEqual(evidence.duckdb_version, duckdb.__version__)
        self.assertEqual(evidence.effective_limits['max_rows'], 12)
        failed = PreparationEvidence()
        with self.assertRaises(duckdb.Error):
            self.service.execute(QueryRequest(sql="SELECT error('failure')"), evidence=failed)
        self.assertIsNotNone(failed.plan)
        self.assertIsNotNone(failed.plan_fingerprint)
        show = PreparationEvidence()
        self.service.prepare(QueryRequest(sql='SHOW TABLES FROM public_v1'), evidence=show)
        self.assertIsNone(show.plan)
        oversized = PreparationEvidence()
        connection = self.service.connection
        class LongPlan:
            def execute(proxy, sql, *args):
                if sql.startswith('EXPLAIN '):
                    return type('Rows', (), {'fetchall': lambda _: [('plan', '界'*30_000)]})()
                return connection.execute(sql, *args)
            def __getattr__(proxy, name):
                return getattr(connection, name)
        with patch.object(self.service, 'connection', LongPlan()):
            self.service.prepare(QueryRequest(sql='SELECT 42'), evidence=oversized)
        self.assertLessEqual(len(oversized.plan.encode()), 64_000)
        self.assertTrue(oversized.plan_truncated)
        self.assertIsNone(oversized.plan_fingerprint)
        self.assertEqual(oversized.diagnostics[0]['code'], 'plan_truncated')

    def test_prepare_execute_and_reuse(self):
        payload = QueryRequest(sql="SELECT page_url FROM public_v1.capture WHERE page_url=?", parameters=["https://example.com/inline"])
        self.assertEqual(self.service.prepare(payload).sql, payload.sql)
        self.assertEqual(self.service.execute(payload).rows, [["https://example.com/inline"]])
        self.assertEqual(self.service.execute(QueryRequest(sql="SELECT count(*) FROM public_v1.capture")).rows, [[21]])

    def test_validation_sandbox_and_recovery(self):
        for method in [self.service.prepare, self.service.execute]:
            for sql in ["DELETE FROM public_v1.capture", "SELECT * FROM read_parquet('/tmp/secret')", "SELECT nonexistent FROM public_v1.capture", "SELECT 1; SELECT 2", "SELECT * FROM ingest.visits", "SELECT getenv('HOME')"]:
                with self.subTest(sql=sql), self.assertRaises((ValueError, duckdb.Error)):
                    method(QueryRequest(sql=sql))
        self.assertEqual(self.service.execute(QueryRequest(sql='SELECT 42')).rows, [[42]])
        for sql in ["DELETE FROM ingest.visits", "SELECT * FROM read_text('/etc/passwd')", "SET enable_external_access=true"]:
            with self.assertRaises(duckdb.Error):
                self.service.connection.execute(sql)

    def test_limits_precision_busy_timeout(self):
        result = self.service.execute(QueryRequest(sql="WITH RECURSIVE t(i) AS (SELECT 0 UNION ALL SELECT i+1 FROM t WHERE i<1100) SELECT i FROM t"))
        self.assertEqual(len(result.rows), 1000)
        self.assertTrue(result.truncated)
        self.assertEqual(self.service.execute(QueryRequest(sql="SELECT 9007199254740993::BIGINT")).rows, [["9007199254740993"]])
        self.service._lock.acquire()
        try:
            with self.assertRaises(BusyError): self.service.execute(QueryRequest(sql='SELECT 1'))
        finally:
            self.service._lock.release()
        self.service.deadline = 0.05
        with self.assertRaises(TimeoutError):
            self.service.execute(QueryRequest(sql="WITH RECURSIVE t(i) AS (SELECT 0 UNION ALL SELECT i+1 FROM t) SELECT sum(i) FROM t"))
        self.service.deadline = 20
        self.assertEqual(self.service.execute(QueryRequest(sql='SELECT 42')).rows, [[42]])

    def test_http_routes_and_auth(self):
        import asyncio
        import os
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from periplus.query.server_http import router, QueryAccessMiddleware
        from periplus.entrypoints.query import healthz
        app = FastAPI()
        app.state.query_limits = AsyncMock()
        app.state.query_limits.read.return_value = QueryLimits()
        app.state.query_service = self.service
        app.state.query_slot = asyncio.Semaphore(1)
        app.add_middleware(QueryAccessMiddleware)
        app.include_router(router)
        app.add_api_route("/healthz", healthz, methods=["GET"])
        with patch.dict(os.environ, {"PERIPLUS_QUERY_API_TOKEN": "query-test"}), TestClient(app) as client:
            headers = {"Authorization": "Bearer query-test"}
            self.assertEqual(client.get('/healthz').status_code, 200)
            self.service.connection.close()
            self.service.connection = None
            self.assertEqual(client.get('/healthz').status_code, 503)
            self.assertEqual(client.post('/query/exec', headers=headers, json={'sql': 'SELECT 1'}).status_code, 200)
            self.assertEqual(client.get('/healthz').status_code, 200)
            self.assertEqual(client.post('/query/exec', json={'sql':'SELECT 1'}).status_code, 401)
            for endpoint in ['prep', 'exec']:
                self.assertEqual(client.post('/query/'+endpoint, headers=headers, json={'sql':'SELECT 1'}).status_code, 200)
                self.assertEqual(client.post('/query/'+endpoint, headers=headers, json={'sql':'DELETE FROM public_v1.capture'}).status_code, 422)
            self.assertEqual(client.get('/query/helpers').status_code, 401)
            helper_response = client.get('/query/helpers', headers=headers)
            self.assertEqual(helper_response.status_code, 200)
            self.assertEqual([h['name'] for h in helper_response.json()['helpers']], ['public_v1.search'])
            self.assertEqual(client.post('/query/helpers', headers=headers).status_code, 404)
            with patch.object(self.service, "execute", side_effect=duckdb.HTTPException("HTTP 404 https://private/file?token=secret")):
                unavailable = client.post('/query/exec', headers=headers, json={'sql': 'SELECT 1'})
            self.assertEqual(unavailable.status_code, 503)
            self.assertEqual(unavailable.json()['code'], 'storage_unavailable')
            self.assertNotIn('secret', unavailable.text)
            invalid = client.post('/query/exec', headers=headers, json={'sql': 'SELECT nonexistent FROM public_v1.capture'})
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(invalid.json()['code'], 'sql_invalid')
            self.assertEqual(client.post('/query/report', headers=headers).status_code, 404)
            self.assertEqual(client.post('/query/exec', headers=headers, content='x'*(16 * 1024 * 1024 + 1)).status_code, 413)
            self.assertEqual(client.get('/graph-runs/', headers=headers).status_code, 404)

    def test_byte_budget_is_independent_of_row_budget(self):
        result = self.service.execute(QueryRequest(sql="SELECT repeat('x', 2000000) AS large_value"),
                                      limits=QueryLimits(max_result_bytes=1024 * 1024))
        self.assertTrue(result.truncated)
        self.assertEqual(result.rows, [])

    def test_operator_limits_apply_per_operation(self):
        payload = QueryRequest(sql="SELECT page_url FROM public_v1.capture ORDER BY page_url")
        for count in (2, 7):
            result = self.service.execute(payload, limits=QueryLimits(max_rows=count))
            self.assertEqual(len(result.rows), count)
            self.assertTrue(result.truncated)
        result = self.service.execute(QueryRequest(sql="WITH RECURSIVE t(i) AS (SELECT 0 UNION ALL SELECT i+1 FROM t WHERE i<1099) SELECT i FROM t"), limits=QueryLimits(max_rows=2000))
        self.assertEqual(len(result.rows), 1100)
        self.assertFalse(result.truncated)
        wide = QueryRequest(sql="SELECT repeat('x', 1500000) AS value")
        self.assertTrue(self.service.execute(wide, limits=QueryLimits(max_result_bytes=1024 * 1024)).truncated)
        self.assertFalse(self.service.execute(wide, limits=QueryLimits(max_result_bytes=2 * 1024 * 1024)).truncated)
        with self.assertRaises(TimeoutError):
            self.service.execute(QueryRequest(sql="WITH RECURSIVE t(i) AS (SELECT 0 UNION ALL SELECT i+1 FROM t) SELECT sum(i) FROM t"), limits=QueryLimits(max_duration_seconds=1))
        self.assertEqual(self.service.execute(QueryRequest(sql='SELECT 42')).rows, [[42]])
        import threading
        with patch('periplus.query.service.threading.Timer', wraps=threading.Timer) as timer:
            self.service.prepare(payload, limits=QueryLimits(max_duration_seconds=120))
            self.assertEqual(timer.call_args.args[0], 120)

    def test_reported_snapshot_pins_the_query_across_a_concurrent_commit(self):
        from periplus.platform.catalogue.connection import DuckLakeConnectionFactory
        # File metadata supports concurrent transactions through one DuckDB
        # instance. Other tests exercise the physically read-only service handle.
        self.service.close()
        writer = DuckLakeConnectionFactory(self.config).connect(read_only=False)
        self.addCleanup(writer.close)
        self.service.connection = writer.cursor()
        self.service.connection.execute("USE periplus")
        connection = self.service.connection
        class CommitAfterSnapshot:
            committed = False
            def execute(proxy, sql, *args):
                if sql.startswith("EXPLAIN") and not proxy.committed:
                    proxy.committed = True
                    writer.execute("BEGIN")
                    writer.execute("INSERT INTO periplus.ingest.visits (visit_id, document_id, requested_url, outcome) VALUES (uuid(), uuid(), 'https://later.example/', 'succeeded')")
                    writer.execute("INSERT INTO periplus.ingest.documents (document_id, visit_id, detected_media_type, content_sha256) SELECT document_id, visit_id, 'text/html', 'later' FROM periplus.ingest.visits WHERE requested_url = 'https://later.example/'")
                    writer.execute("COMMIT")
                return connection.execute(sql, *args)
            def __getattr__(proxy, name):
                return getattr(connection, name)
        self.service.connection = CommitAfterSnapshot()
        try:
            first = self.service.execute(QueryRequest(sql="SELECT count(*) FROM public_v1.capture"))
            second = self.service.execute(QueryRequest(sql="SELECT count(*) FROM public_v1.capture"))
        finally:
            self.service.connection = connection
        self.assertEqual(first.rows, [[21]])
        self.assertEqual(second.rows, [[22]])
        self.assertGreater(second.source_snapshot, first.source_snapshot)

    def test_fatal_failure_discards_connection_without_replaying_query(self):
        original = self.service.connection
        calls = []
        class Poisoned:
            def execute(proxy, sql, *args):
                calls.append(sql)
                raise duckdb.FatalException("database invalidated")
            def interrupt(proxy):
                original.interrupt()
            def close(proxy):
                original.close()
        self.service.connection = Poisoned()
        with self.assertRaises(duckdb.FatalException):
            self.service.execute(QueryRequest(sql="SELECT 42"))
        self.assertFalse(self.service.healthy)
        self.assertEqual(calls, ["BEGIN TRANSACTION", "ROLLBACK"])
        self.assertEqual(self.service.execute(QueryRequest(sql="SELECT count(*) FROM public_v1.capture")).rows, [[21]])
        self.assertTrue(self.service.healthy)
        for sql in ["SET enable_external_access=true", "DELETE FROM ingest.visits", "SELECT * FROM read_text('/etc/passwd')"]:
            with self.assertRaises(duckdb.Error):
                self.service.connection.execute(sql)

    def test_poisoned_cleanup_preserves_original_error_and_recovery_releases_slot(self):
        original = self.service.connection
        class Poisoned:
            def execute(proxy, sql, *args):
                if sql == "ROLLBACK":
                    raise duckdb.FatalException("invalidated cleanup")
                raise duckdb.InvalidInputException("original failure")
            def interrupt(proxy):
                original.interrupt()
            def close(proxy):
                original.close()
        self.service.connection = Poisoned()
        with self.assertRaisesRegex(duckdb.InvalidInputException, "original failure"):
            self.service.prepare(QueryRequest(sql="SELECT 1"))
        self.assertFalse(self.service.healthy)
        with patch.object(self.service, "_connect", side_effect=duckdb.IOException("offline")):
            with self.assertRaises(duckdb.IOException):
                self.service.execute(QueryRequest(sql="SELECT 1"))
        self.assertFalse(self.service._lock.locked())
        self.assertEqual(self.service.execute(QueryRequest(sql="SELECT 1")).rows, [[1]])
