"""Transport safety at the uncertain-write boundary."""
import unittest
import json
from unittest.mock import patch

import httpx
from pydantic import SecretStr

from periplus.platform.clickhouse import ClickHouseClient, ClickHouseConfig, ClickHouseError


class ClickHouseClientTests(unittest.TestCase):
    def test_prepared_material_row_reuses_exact_wire_bytes(self):
        from periplus.materialization.storage import output_row
        row = output_row({'document_text': '猫😀'})
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, content=b'')
        client = self.client(handler)
        client._input_schemas['material.test'] = {'document_text': 'String', 'output_digest': 'FixedString(32)'}
        with patch('periplus.platform.clickhouse.client.json.dumps', side_effect=AssertionError('Must reuse prepared bytes')):
            client.insert_rows('material.test', [row])
        self.assertEqual(requests[0].content.split(b'FORMAT JSONEachRow\n', 1)[-1], row.wire)

    def client(self, handler):
        client = ClickHouseClient(
            ClickHouseConfig(url="http://localhost:8123", username="test", password=SecretStr("private")),
            transport=httpx.MockTransport(handler),
        )
        self.addCleanup(client.close)
        return client

    def test_disconnected_insert_is_not_retried(self):
        requests = []

        def disconnected(request):
            requests.append(request)
            raise httpx.RemoteProtocolError("connection closed after insert")

        client = self.client(disconnected)
        with self.assertRaises(ClickHouseError) as raised:
            client.execute("INSERT INTO ingest.visits FORMAT JSONEachRow", data=b"{}\n", query_id="write-1")
        self.assertEqual(len(requests), 1)
        self.assertEqual(raised.exception.query_id, "write-1")
        self.assertEqual(raised.exception.code, "transport")

    def test_server_failure_does_not_disclose_query_or_error_body(self):
        client = self.client(lambda request: httpx.Response(
            500, headers={"X-ClickHouse-Exception-Code": "241"}, content=b"sensitive server detail",
        ))
        with self.assertRaises(ClickHouseError) as raised:
            client.execute("SELECT 'private query'", query_id="query-1")
        self.assertEqual(raised.exception.code, "241")
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertNotIn("private", str(raised.exception))

    def test_redirect_is_rejected_without_sending_credentials_elsewhere(self):
        requests = []

        def redirect(request):
            requests.append(request)
            return httpx.Response(307, headers={"Location": "http://other.example/"})

        with self.assertRaises(ClickHouseError):
            self.client(redirect).execute("SELECT 1")
        self.assertEqual(len(requests), 1)

    def test_response_bound_is_enforced(self):
        client = self.client(lambda request: httpx.Response(200, content=b"12345"))
        with self.assertRaises(ClickHouseError) as raised:
            client.execute("SELECT 1", max_response_bytes=4)
        self.assertEqual(raised.exception.code, "response_limit")

    def test_parameter_values_remain_separate_from_sql(self):
        requests = []

        def response(request):
            requests.append(request)
            return httpx.Response(200, json={"data": [{"value": "' OR 1=1"}]})

        sql = "SELECT {value:String} AS value"
        result = self.client(response).query(sql, parameters={"value": "' OR 1=1"})
        self.assertEqual(result["data"], [{"value": "' OR 1=1"}])
        params = requests[0].url.params
        self.assertEqual(requests[0].content.decode(), sql + " FORMAT JSON")
        self.assertEqual(params["param_value"], "' OR 1=1")
        self.assertEqual(params["async_insert"], "0")
        self.assertEqual(params["wait_end_of_query"], "1")

    def test_insert_batches_use_exact_compact_utf8_bytes(self):
        requests = []
        def receive(request):
            requests.append(request)
            return httpx.Response(200, content=b"")
        client = self.client(receive)
        client._input_schemas["material.test"] = {"value": "String"}
        rows = [{"value": "å" * 12}, {"value": "x" * 90}, {"value": "tail"}]
        with patch("periplus.platform.clickhouse.client.INSERT_TARGET_BYTES", 50):
            client.insert_rows("material.test", rows)
        expected = [(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode() for row in rows]
        self.assertEqual([request.content for request in requests], expected)
        self.assertEqual([json.loads(request.content) for request in requests], rows)

    def test_insert_rejects_oversized_single_row_before_upload(self):
        requests = []
        client = self.client(lambda request: requests.append(request))
        client._input_schemas["material.test"] = {"value": "String"}
        with patch("periplus.platform.clickhouse.client.MAX_INSERT_BYTES", 32):
            with self.assertRaisesRegex(ValueError, "row material.test/unknown is 113 bytes; limit is 32"):
                client.insert_rows("material.test", [{"value": "x" * 100}])
        self.assertEqual(requests, [])

    def test_request_limit_reports_actual_bytes(self):
        client = self.client(lambda request: self.fail("must not upload"))
        with self.assertRaisesRegex(ValueError, "5 bytes; limit is 4"):
            client.execute("INSERT", data=b"12345", max_request_bytes=4)


if __name__ == "__main__":
    unittest.main()
