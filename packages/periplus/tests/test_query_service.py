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
            "INSERT INTO ingest.visits (visit_id, requested_url, outcome) SELECT uuid(), 'https://example.com/' || i, 'success' FROM range(20) t(i)"
        )
        d.execute(
            "INSERT INTO ingest.visits (visit_id, requested_url, outcome) VALUES (uuid(), 'https://example.com/inline', 'success')"
        )
        d.close()
        self.service = QueryService(self.config)
        self.addCleanup(self.service.close)

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
            self.assertEqual(client.post('/query/report', headers=headers).status_code, 404)
            self.assertEqual(client.post('/query/exec', headers=headers, content='x'*140000).status_code, 413)
            self.assertEqual(client.get('/graph-runs/', headers=headers).status_code, 404)

    def test_byte_budget_is_independent_of_row_budget(self):
        with patch('periplus.query.service.MAX_RESPONSE_BYTES', 2048):
            result = self.service.execute(QueryRequest(sql="SELECT repeat('x', 3000) AS large_value"))
        self.assertTrue(result.truncated)
        self.assertEqual(result.rows, [])
