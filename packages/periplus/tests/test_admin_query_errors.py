"""Operator reads must not be described as uncertain writes."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from periplus.operations.api.catalogue import admin_sql
from periplus.platform.clickhouse import ClickHouseError
from periplus.query.models import QueryRequest


class AdminQueryErrorsTests(unittest.IsolatedAsyncioTestCase):
    async def failure(self, sql, code):
        request = SimpleNamespace(state=SimpleNamespace(api_role="admin"),
            app=SimpleNamespace(state=SimpleNamespace(admin_sql_slot=asyncio.Semaphore(1))))
        client = Mock()
        client.execute.side_effect = ClickHouseError("test-id", code=code)
        with patch("periplus.platform.clickhouse.connect_clickhouse", return_value=client), patch("periplus.operations.api.catalogue.BuildControl") as control:
            control.return_value.binding.return_value = {"database":"public_v1","revision":0,"expires_at":"2099-01-01T00:00:00Z"}
            with self.assertRaises(HTTPException) as raised:
                await admin_sql(QueryRequest(sql=sql), request)
        client.close.assert_called_once()
        return raised.exception

    async def test_select_memory_failure_is_a_read_budget_error(self):
        error = await self.failure("SELECT * FROM public_v1.html_element LIMIT 10", "241")
        self.assertEqual(error.status_code, 422)
        self.assertIn("memory budget", error.detail)
        self.assertNotIn("rollback", error.detail)

    async def test_uncertain_write_warning_is_preserved(self):
        error = await self.failure("DROP TABLE material.test", "transport")
        self.assertIn("does not establish rollback", error.detail)
