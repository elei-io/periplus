import unittest
from unittest.mock import patch
import httpx
from periplus_sdk import configure
from periplus_sdk._http import request
from periplus_sdk.errors import ApiError, ConflictError


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_after_without_mutation_replay(self):
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(503, json={"detail": "History unavailable"}, headers={"Retry-After": "5"})
        configure(api_url="https://periplus.example", api_token="test-token")
        client_type = httpx.AsyncClient
        with patch("periplus_sdk._http.httpx.AsyncClient", side_effect=lambda **kwargs:
                   client_type(**kwargs, transport=httpx.MockTransport(respond))):
            with self.assertRaises(ApiError) as raised:
                await request("POST", "/collections", json={})
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.retry_after_seconds, 5)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].headers['Authorization'], 'Bearer test-token')

    async def test_stale_control_update_preserves_conflict(self):
        configure(api_url="https://periplus.example", api_token="test-token")
        client_type = httpx.AsyncClient
        with patch("periplus_sdk._http.httpx.AsyncClient", side_effect=lambda **kwargs:
                   client_type(**kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(409, json={"detail": "Reload controls"})))):
            with self.assertRaises(ConflictError):
                await request("PUT", "/frontier/controls", json={})
