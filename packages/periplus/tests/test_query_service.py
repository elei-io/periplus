from periplus.operations.access.schemas import QueryLimits
from unittest.mock import AsyncMock
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import duckdb

from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.connection import _literal
from periplus.platform.catalogue.public import public_objects
from periplus.platform.catalogue.schema import expected_columns
from periplus.platform.catalogue.client import _column_type
from periplus.query.service import QueryService, QueryRequest, BusyError


class QueryServiceTests(unittest.TestCase):
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
        for schema in ("ingest", "material", "public_v1"):
            d.execute(f"CREATE SCHEMA {schema}")
        for relation, columns in expected_columns().items():
            definitions = ", ".join(
                f'"{name}" {_column_type(column)}' for name, column in columns.items()
            )
            d.execute(f"CREATE TABLE {relation.qualified} ({definitions})")
        base = files("periplus.platform.catalogue").joinpath("sql")
        for item in public_objects():
            d.execute(base.joinpath(item.schema, item.resource).read_text())
        d.execute(
            "INSERT INTO ingest.visits (visit_id, requested_url, outcome) SELECT uuid(), 'https://example.com/' || i, 'success' FROM range(20) t(i)"
        )
        d.execute(
            "INSERT INTO ingest.visits (visit_id, requested_url, outcome) VALUES (uuid(), 'https://example.com/inline', 'success')"
        )
        d.execute("UPDATE ingest.visits SET document_id = '00000000-0000-0000-0000-000000000001' WHERE requested_url = 'https://example.com/inline'")
        d.execute("INSERT INTO ingest.documents (document_id, visit_id, detected_media_type, content_sha256) SELECT document_id, visit_id, 'text/html', 'helper-fixture' FROM ingest.visits WHERE document_id IS NOT NULL")
        d.execute("INSERT INTO material.html_elements (content_sha256, element_index, subtree_end_index, depth, text_direct) VALUES ('helper-fixture',0,2,0,'start'),('helper-fixture',1,2,1,'nested')")
        d.execute("UPDATE ingest.visits SET document_id = uuid() WHERE document_id IS NULL")
        d.execute("INSERT INTO ingest.documents (document_id, visit_id, detected_media_type, content_sha256) SELECT document_id, visit_id, 'text/html', visit_id::VARCHAR FROM ingest.visits WHERE requested_url <> 'https://example.com/inline'")
        d.execute("INSERT INTO material.html_nodes (content_sha256, node_index, subtree_end_index, node_type, value, depth) VALUES ('helper-fixture',0,4,'element',NULL,0),('helper-fixture',1,2,'text','start',1),('helper-fixture',2,3,'text','nested',1),('helper-fixture',3,4,'text','end',1)")
        d.execute("UPDATE material.html_elements SET tag = 'title', namespace = 'HTML' WHERE content_sha256 = 'helper-fixture' AND element_index = 0")
        d.execute("INSERT INTO material.prose VALUES ('helper-fixture', 'robot careers')")
        d.close()
        self.service = QueryService(self.config)
        self.addCleanup(self.service.close)

    def test_content_scope_preparation_execution_and_reuse(self):
        from periplus.operations.query_history.schemas import PreparationEvidence
        request = QueryRequest(sql="""SELECT c.requested_url AS url, m.value AS title
            FROM prose p JOIN capture c USING (content_id)
            JOIN html_metadata m USING (content_id)
            WHERE p.text ILIKE ? AND m.name = ?""", parameters=['%robot%', 'title'])
        expected = self.service.connection.execute(request.sql, request.parameters).fetchall()
        evidence = PreparationEvidence()
        prepared = self.service.prepare(request, evidence=evidence)
        self.assertIn('content_scope', [d.code for d in prepared.diagnostics])
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
        # Binding the original request must happen before an attempted rewrite.
        with self.assertRaises(duckdb.BinderException):
            self.service.prepare(QueryRequest(sql=request.sql.replace('m.value', 'm.missing'), parameters=request.parameters))

    def test_content_scope_definition_mismatch_keeps_original(self):
        request = QueryRequest(sql="""SELECT m.* FROM prose p JOIN html_metadata m USING (content_id)
            WHERE p.text ILIKE '%robot%'""")
        with patch('periplus.query.content_scope.ContentScope.matches', return_value=False):
            prepared = self.service.prepare(request)
            self.assertEqual(prepared.sql, request.sql)
            self.assertNotIn('content_scope', [d.code for d in prepared.diagnostics])

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

    def test_persisted_helper_executes_through_read_only_query_api(self):
        request = QueryRequest(sql="SELECT * FROM public_v1.subtree_text(?, ?, max_chars := ?)", parameters=["helper-fixture", 0, 10])
        self.assertEqual(self.service.prepare(request).sql, request.sql)
        result = self.service.execute(request)
        self.assertEqual(result.columns, ["text", "truncated", "total_chars", "node_count"])
        self.assertEqual(result.rows, [["startneste", True, 14, 4]])

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
        payload = QueryRequest(sql="SELECT requested_url FROM public_v1.capture WHERE requested_url=?", parameters=["https://example.com/inline"])
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
            self.assertEqual(helper_response.json()['helpers'][0]['name'], 'public_v1.subtree_text')
            self.assertEqual(client.post('/query/helpers', headers=headers).status_code, 404)
            limited = client.post('/query/exec', headers=headers, json={'sql': "SELECT * FROM public_v1.subtree_text('helper-fixture',0,max_nodes := 1)"})
            self.assertEqual(limited.status_code, 422)
            self.assertEqual(limited.json()['detail'], 'subtree exceeds max_nodes; select a smaller root')
            with patch.object(self.service, "execute", side_effect=duckdb.HTTPException("HTTP 404 https://private/file?token=secret")):
                unavailable = client.post('/query/exec', headers=headers, json={'sql': 'SELECT 1'})
            self.assertEqual(unavailable.status_code, 503)
            self.assertEqual(unavailable.json()['code'], 'storage_unavailable')
            self.assertNotIn('secret', unavailable.text)
            invalid = client.post('/query/exec', headers=headers, json={'sql': 'SELECT nonexistent FROM public_v1.capture'})
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(invalid.json()['code'], 'sql_invalid')
            self.assertEqual(client.post('/query/report', headers=headers).status_code, 404)
            self.assertEqual(client.post('/query/exec', headers=headers, content='x'*140000).status_code, 413)
            self.assertEqual(client.get('/graph-runs/', headers=headers).status_code, 404)

    def test_byte_budget_is_independent_of_row_budget(self):
        result = self.service.execute(QueryRequest(sql="SELECT repeat('x', 2000000) AS large_value"),
                                      limits=QueryLimits(max_result_bytes=1024 * 1024))
        self.assertTrue(result.truncated)
        self.assertEqual(result.rows, [])

    def test_operator_limits_apply_per_operation(self):
        payload = QueryRequest(sql="SELECT requested_url FROM public_v1.capture ORDER BY requested_url")
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
