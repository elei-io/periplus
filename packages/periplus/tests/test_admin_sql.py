import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import duckdb
from fastapi import FastAPI
from fastapi.testclient import TestClient

from periplus.platform.api_access import ApiAccessMiddleware
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.query.admin import AdminSqlService, router
from periplus.query.service import BusyError, QueryRequest


class AdminSqlTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.service = AdminSqlService(CatalogueConfig(
            "periplus", str(root / "lake.duckdb"), str(root / "data"), "ducklake",
        ))

    def run_sql(self, sql, parameters=None):
        return self.service.execute(QueryRequest(sql=sql, parameters=parameters or []))

    def test_writable_script_commits_and_reads_private_schema(self):
        result = self.run_sql("CREATE SCHEMA ingest; CREATE TABLE ingest.admin_test (id BIGINT); INSERT INTO ingest.admin_test VALUES (7); SELECT * FROM ingest.admin_test")
        self.assertEqual(result.rows, [[7]])
        self.assertEqual(self.run_sql("SELECT * FROM ingest.admin_test").rows, [[7]])
        self.run_sql("UPDATE ingest.admin_test SET id = ?", [9])
        self.assertEqual(self.run_sql("SELECT * FROM ingest.admin_test").rows, [[9]])
        self.run_sql("DROP TABLE ingest.admin_test")

    def test_failed_script_rolls_back_and_session_does_not_leak(self):
        self.run_sql("CREATE TABLE sample (id INTEGER)")
        with self.assertRaises(duckdb.Error):
            self.run_sql("INSERT INTO sample VALUES (1); SELECT * FROM missing_table")
        self.assertEqual(self.run_sql("SELECT count(*) FROM sample").rows, [[0]])
        self.run_sql("CREATE TEMP TABLE ephemeral (id INTEGER)")
        with self.assertRaises(duckdb.Error):
            self.run_sql("SELECT * FROM ephemeral")
        self.assertEqual(self.run_sql("SELECT 42").rows, [[42]])

    def test_transaction_controls_rejected_before_any_write(self):
        self.run_sql("CREATE TABLE sample (id INTEGER)")
        for sql in ("INSERT INTO sample VALUES (1); COMMIT", "BEGIN; SELECT 1", "ROLLBACK"):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                self.run_sql(sql)
        self.assertEqual(self.run_sql("SELECT count(*) FROM sample").rows, [[0]])

    def test_limits_busy_and_parameter_contract(self):
        result = self.run_sql("SELECT * FROM range(1002)")
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.rows), 1000)
        self.assertEqual(self.run_sql("SELECT ?::BIGINT", [9007199254740993]).rows, [["9007199254740993"]])
        with self.assertRaises(ValueError):
            self.run_sql("SELECT ?; SELECT 2", [1])
        with self.assertRaises(ValueError):
            self.run_sql("-- empty")
        with self.service._lock, self.assertRaises(BusyError):
            self.run_sql("SELECT 1")
        self.assertEqual(self.run_sql("SELECT 2").rows, [[2]])

    def test_truncated_result_still_commits_all_rows(self):
        self.run_sql("CREATE TABLE sample (id BIGINT)")
        result = self.run_sql("INSERT INTO sample SELECT * FROM range(1200); SELECT id FROM sample")
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.rows), 1000)
        self.assertEqual(self.run_sql("SELECT count(*) FROM sample").rows, [[1200]])
        with patch("periplus.query.admin.MAX_RESPONSE_BYTES", 2048):
            result = self.run_sql("SELECT repeat('x', 1500)")
        self.assertTrue(result.truncated)
        self.assertEqual(result.rows, [])

    def test_http_admin_only_and_busy(self):
        app = FastAPI()
        app.add_middleware(ApiAccessMiddleware)
        app.include_router(router)
        app.state.admin_sql = self.service
        app.state.admin_sql_slot = asyncio.Semaphore(1)
        with patch.dict(os.environ, {"PERIPLUS_ADMIN_API_TOKEN": "admin-test", "PERIPLUS_PUBLIC_API_TOKEN": "public-test"}), TestClient(app) as client:
            path = "/admin/sql/exec"
            payload = {"sql": "SELECT 1"}
            self.assertEqual(client.post(path, json=payload).status_code, 401)
            self.assertEqual(client.post(path, json=payload, headers={"Authorization": "Bearer public-test"}).status_code, 403)
            headers = {"Authorization": "Bearer admin-test"}
            self.assertEqual(client.post(path, json=payload, headers=headers).json()["rows"], [[1]])
            app.state.admin_sql_slot = asyncio.Semaphore(0)
            self.assertEqual(client.post(path, json=payload, headers=headers).status_code, 429)
            app.state.admin_sql_slot = asyncio.Semaphore(1)
            self.assertEqual(client.post(path, content=b"x" * (512 * 1024 + 1), headers=headers).status_code, 413)
