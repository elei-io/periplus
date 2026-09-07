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
            "",
        )
        d = duckdb.connect()
        d.execute("LOAD ducklake")
        d.execute(
            f"ATTACH {_literal('ducklake:' + self.config.metadata_path)} AS periplus (DATA_PATH {_literal(self.config.data_path)}, METADATA_SCHEMA 'ducklake')"
        )
        d.execute("USE periplus")
        for schema in ("ingest", "material", "web", "content"):
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
            "INSERT INTO ingest.visits (visit_id, visibility, requested_url, outcome) SELECT uuid(), 'public', 'https://example.com/' || i, 'success' FROM range(20) t(i)"
        )
        d.execute(
            "INSERT INTO ingest.visits (visit_id, visibility, requested_url, outcome) VALUES (uuid(), 'public', 'https://example.com/inline', 'success')"
        )
        d.execute("UPDATE ingest.visits SET document_id = '00000000-0000-0000-0000-000000000001' WHERE requested_url = 'https://example.com/inline'")
        d.execute("INSERT INTO ingest.documents (document_id, visit_id, content_sha256) SELECT document_id, visit_id, 'helper-fixture' FROM ingest.visits WHERE document_id IS NOT NULL")
        d.execute("INSERT INTO material.html_elements (content_sha256, element_index, subtree_end_index, depth, text_direct, text_tail) VALUES ('helper-fixture',0,2,0,'start','outside'),('helper-fixture',1,2,1,'nested','end')")
        d.close()
        self.service = QueryService(self.config)
        self.addCleanup(self.service.close)

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
        observation = self.service.execute(QueryRequest(sql="SELECT observation_id FROM web.observation LIMIT 1")).rows[0][0]
        found = self.service.execute(QueryRequest(sql="SELECT count(*) AS n FROM web.observation WHERE observation_id = ?::UUID", parameters=[observation]))
        self.assertEqual(found.rows, [[1]])
        for forbidden in ("SELECT ?::INTEGER; SELECT 2", "SELECT ?::INTEGER FROM ingest.visits"):
            with self.subTest(sql=forbidden), self.assertRaises(ValueError):
                self.service.prepare(QueryRequest(sql=forbidden, parameters=[7]))

    def test_persisted_helper_executes_through_read_only_query_api(self):
        request = QueryRequest(sql="SELECT * FROM content.subtree_text(?, ?, max_chars := ?)", parameters=["helper-fixture", 0, 10])
        self.assertEqual(self.service.prepare(request).sql, request.sql)
        result = self.service.execute(request)
        self.assertEqual(result.columns, ["text", "truncated", "total_chars", "element_count"])
        self.assertEqual(result.rows, [["startneste", True, 14, 2]])

    def test_prepare_execute_and_reuse(self):
        payload = QueryRequest(sql="SELECT requested_url FROM web.observation WHERE requested_url=?", parameters=["https://example.com/inline"])
        self.assertEqual(self.service.prepare(payload).sql, payload.sql)
        self.assertEqual(self.service.execute(payload).rows, [["https://example.com/inline"]])
        self.assertEqual(self.service.execute(QueryRequest(sql="SELECT count(*) FROM web.observation")).rows, [[21]])

    def test_validation_sandbox_and_recovery(self):
        for method in [self.service.prepare, self.service.execute]:
            for sql in ["DELETE FROM web.observation", "SELECT * FROM read_parquet('/tmp/secret')", "SELECT nonexistent FROM web.observation", "SELECT 1; SELECT 2", "SELECT * FROM ingest.visits", "SELECT getenv('HOME')"]:
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
        app = FastAPI()
        app.state.query_service = self.service
        app.state.query_slot = asyncio.Semaphore(1)
        app.add_middleware(QueryAccessMiddleware)
        app.include_router(router)
        with patch.dict(os.environ, {"PERIPLUS_QUERY_API_TOKEN": "query-test"}), TestClient(app) as client:
            headers = {"Authorization": "Bearer query-test"}
            self.assertEqual(client.post('/query/exec', json={'sql':'SELECT 1'}).status_code, 401)
            for endpoint in ['prep', 'exec']:
                self.assertEqual(client.post('/query/'+endpoint, headers=headers, json={'sql':'SELECT 1'}).status_code, 200)
                self.assertEqual(client.post('/query/'+endpoint, headers=headers, json={'sql':'DELETE FROM web.observation'}).status_code, 422)
            self.assertEqual(client.get('/query/helpers').status_code, 401)
            helper_response = client.get('/query/helpers', headers=headers)
            self.assertEqual(helper_response.status_code, 200)
            self.assertEqual(helper_response.json()['helpers'][0]['name'], 'content.subtree_text')
            self.assertEqual(client.post('/query/helpers', headers=headers).status_code, 404)
            limited = client.post('/query/exec', headers=headers, json={'sql': "SELECT * FROM content.subtree_text('helper-fixture',0,max_elements := 1)"})
            self.assertEqual(limited.status_code, 422)
            self.assertEqual(limited.json()['detail'], 'subtree exceeds max_elements; select a smaller root')
            with patch.object(self.service, "execute", side_effect=duckdb.HTTPException("HTTP 404 https://private/file?token=secret")):
                unavailable = client.post('/query/exec', headers=headers, json={'sql': 'SELECT 1'})
            self.assertEqual(unavailable.status_code, 503)
            self.assertEqual(unavailable.json()['code'], 'storage_unavailable')
            self.assertNotIn('secret', unavailable.text)
            invalid = client.post('/query/exec', headers=headers, json={'sql': 'SELECT nonexistent FROM web.observation'})
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(invalid.json()['code'], 'sql_invalid')
            self.assertEqual(client.post('/query/report', headers=headers).status_code, 404)
            self.assertEqual(client.post('/query/exec', headers=headers, content='x'*140000).status_code, 413)
            self.assertEqual(client.get('/graph-runs/', headers=headers).status_code, 404)

    def test_byte_budget_is_independent_of_row_budget(self):
        with patch('periplus.query.service.MAX_RESPONSE_BYTES', 2048):
            result = self.service.execute(QueryRequest(sql="SELECT repeat('x', 3000) AS large_value"))
        self.assertTrue(result.truncated)
        self.assertEqual(result.rows, [])

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
                    writer.execute("INSERT INTO periplus.ingest.visits (visit_id, visibility, requested_url, outcome) VALUES (uuid(), 'public', 'https://later.example/', 'succeeded')")
                return connection.execute(sql, *args)
            def __getattr__(proxy, name):
                return getattr(connection, name)
        self.service.connection = CommitAfterSnapshot()
        try:
            first = self.service.execute(QueryRequest(sql="SELECT count(*) FROM web.observation"))
            second = self.service.execute(QueryRequest(sql="SELECT count(*) FROM web.observation"))
        finally:
            self.service.connection = connection
        self.assertEqual(first.rows, [[21]])
        self.assertEqual(second.rows, [[22]])
        self.assertGreater(second.source_snapshot, first.source_snapshot)

    def test_background_seen_uses_public_terminal_requested_urls_and_successful_effective_urls(self):
        from periplus.platform.catalogue.connection import DuckLakeConnectionFactory
        from periplus.crawl.runtime.background_seen import SeenCandidates, lookup_seen
        from periplus.crawl.runtime.selection_contract import SelectionCheckpoint
        self.service.close()
        writer = DuckLakeConnectionFactory(self.config).connect(read_only=False)
        writer.execute("USE periplus")
        writer.execute("""
            INSERT INTO ingest.visits (visit_id, visibility, requested_url, effective_url, outcome)
            VALUES
                (uuid(), 'public', 'https://requested.example/', 'https://redirect.example/', 'succeeded'),
                (uuid(), 'public', 'https://failed.example/', 'https://failed-effective.example/', 'failed'),
                (uuid(), 'private', 'https://private.example/', 'https://private-effective.example/', 'succeeded')
        """)
        writer.close()
        self.service = QueryService(self.config)
        self.addCleanup(self.service.close)
        def select(request):
            result = self.service.execute(request)
            self.assertFalse(result.truncated)
            return SelectionCheckpoint(urls=tuple(row[0] for row in result.rows),
                                       source_snapshot=str(result.source_snapshot), source_query_id=result.query_id)
        result = lookup_seen(SeenCandidates(urls=(
            "https://requested.example/", "https://redirect.example/", "https://failed.example/",
            "https://failed-effective.example/", "https://private.example/", "https://private-effective.example/",
            "https://unseen.example/",
        )), select)
        self.assertEqual(set(result.seen_urls), {
            "https://requested.example/", "https://redirect.example/", "https://failed.example/",
        })
        self.assertGreater(result.snapshot, 0)
