"""Transport safety at the uncertain-write boundary."""
import unittest

import httpx
from pydantic import SecretStr

from periplus.platform.clickhouse import ClickHouseClient, ClickHouseConfig, ClickHouseError


class ClickHouseClientTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
