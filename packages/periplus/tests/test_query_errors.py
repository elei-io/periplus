import unittest

import duckdb

from periplus.query.errors import query_error
from periplus.query.service import BusyError


class QueryErrorTests(unittest.TestCase):
    def test_native_error_messages_never_escape(self):
        secret = "https://private/key?token=secret password=secret"
        for error, status, code in [
            (duckdb.HTTPException(secret), 503, "storage_unavailable"),
            (duckdb.IOException(secret), 503, "storage_unavailable"),
            (duckdb.BinderException(secret), 422, "sql_invalid"),
            (duckdb.ParserException(secret), 422, "sql_invalid"),
            (duckdb.ConversionException(secret), 422, "sql_invalid"),
            (duckdb.OutOfMemoryException(secret), 422, "resource_limit"),
            (TimeoutError(secret), 408, "resource_limit"),
            (BusyError(secret), 429, "service_busy"),
            (duckdb.InternalException(secret), 500, "query_failed"),
        ]:
            with self.subTest(code=code):
                actual_status, body = query_error(error)
                self.assertEqual(actual_status, status)
                self.assertEqual(body.code, code)
                self.assertNotIn("secret", body.model_dump_json())
                self.assertNotIn("private", body.model_dump_json())

    def test_helper_and_public_validation_messages_remain_actionable(self):
        status, body = query_error(duckdb.InvalidInputException("Invalid Input Error: subtree exceeds max_elements; select a smaller root"))
        self.assertEqual(status, 422)
        self.assertEqual(body.code, "helper_limit")
        self.assertEqual(body.detail, "subtree exceeds max_elements; select a smaller root")
        self.assertEqual(query_error(ValueError("Only read-only SQL is allowed"))[1].code, "sql_invalid")

    def test_parser_error_provides_safe_alias_guidance(self):
        status, body = query_error(duckdb.ParserException("syntax error near class; SELECT private_payload"))
        self.assertEqual(status, 422)
        self.assertEqual(body.code, "sql_invalid")
        self.assertIn("Use AS", body.detail)
        self.assertNotIn("private_payload", body.detail)
