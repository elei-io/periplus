import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from periplus.platform.api_access import ApiAccessMiddleware


class ApiAccessTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "PERIPLUS_ADMIN_API_TOKEN": "admin-test-token",
            "PERIPLUS_PUBLIC_API_TOKEN": "public-test-token",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        app = FastAPI()
        app.add_middleware(ApiAccessMiddleware)

        @app.api_route("/{path:path}", methods=["GET", "POST", "DELETE"])
        def endpoint(path: str):
            return {"ok": True}

        self.client = TestClient(app)

    def test_missing_and_invalid_credentials_are_rejected(self):
        for headers in [{}, {"Authorization": "Bearer wrong"}]:
            self.assertEqual(self.client.get("/graph-runs/", headers=headers).status_code, 401)

    def test_public_service_has_only_explicit_capabilities(self):
        headers = {"Authorization": "Bearer public-test-token"}
        for method, path in [("POST", "/collections"), ("GET", "/collections"), ("GET", "/collections/history"), ("GET", f"/collections/{uuid4()}")]:
            self.assertEqual(self.client.request(method, path, headers=headers).status_code, 200)
        for method, path in [("POST", "/collections/history"), ("GET", "/collections/history/private"), ("POST", "/crawls/"), ("GET", f"/crawls/{uuid4()}"), ("DELETE", "/collections"), ("POST", "/query/exec"), ("POST", "/sql/query"), ("POST", f"/query/browser/{uuid4()}/metadata"), ("GET", "/graph-runs/"), ("POST", "/materializations/rebuild"), ("POST", "/crawl-plans/"), ("DELETE", "/crawls/"), ("GET", "/sql/metadata"), ("GET", "/openapi.json")]:
            self.assertEqual(self.client.request(method, path, headers=headers).status_code, 403)

    def test_public_content_access_is_hash_scoped_and_read_only(self):
        headers = {"Authorization": "Bearer public-test-token"}
        path = "/documents/by-content/" + "a" * 64 + "/content"
        self.assertEqual(self.client.get(path, headers=headers).status_code, 200)
        for method, target in [("POST", path), ("GET", "/documents"),
                               ("GET", f"/documents/{uuid4()}/content"),
                               ("GET", "/documents/by-content/invalid/content")]:
            self.assertEqual(self.client.request(method, target, headers=headers).status_code, 403)

    def test_admin_can_reach_operational_routes(self):
        self.assertEqual(self.client.post("/materializations/rebuild", headers={"Authorization": "Bearer admin-test-token"}).status_code, 200)

    def test_equal_or_missing_tokens_fail_closed(self):
        for value in ["", "admin-test-token"]:
            with patch.dict(os.environ, {"PERIPLUS_PUBLIC_API_TOKEN": value}):
                self.assertEqual(self.client.post("/query/exec", headers={"Authorization": "Bearer admin-test-token"}).status_code, 503)
