import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from periplus.crawl.api.coverage_requests import router
from periplus.crawl.control.coverage_requests.models import CoverageRequest
from periplus.platform.api_access import ApiAccessMiddleware
from periplus.platform.postgres.session import get_session


class CoverageRequestTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        CoverageRequest.__table__.create(self.engine)
        self.addCleanup(self.engine.dispose)
        app = FastAPI()
        app.include_router(router)
        app.add_middleware(ApiAccessMiddleware)
        self.env = patch.dict(os.environ, {"PERIPLUS_ADMIN_API_TOKEN": "admin", "PERIPLUS_PUBLIC_API_TOKEN": "public"})
        self.env.start()
        self.addCleanup(self.env.stop)

        def session_override():
            with Session(self.engine, expire_on_commit=False) as session:
                with session.begin():
                    yield session
        app.dependency_overrides[get_session] = session_override
        self.client = TestClient(app, headers={"Authorization": "Bearer public"})
        self.addCleanup(self.client.close)
        self.payload = {"kind": "url", "input": "https://example.org/", "depth": 2, "link_scope": "both", "max_pages": 1000}

    def test_create_persists_pending_and_can_be_read_without_runtime(self):
        response = self.client.post("/coverage-requests", json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertEqual(data["status"], "pending")
        self.assertIsNone(data["completed_at"])
        with Session(self.engine) as session:
            row = session.scalar(select(CoverageRequest))
            self.assertEqual(str(row.id), data["id"])
            self.assertEqual(row.max_pages, 1000)
        detail = self.client.get(f'/coverage-requests/{data["id"]}').json()
        # SQLite drops timezone metadata; production Postgres retains it.
        self.assertEqual(detail["created_at"].removesuffix("Z"), data["created_at"].removesuffix("Z"))
        self.assertEqual({k: v for k, v in detail.items() if k != "created_at"}, {k: v for k, v in data.items() if k != "created_at"})
        self.assertEqual(self.client.get("/coverage-requests").json()["items"], [detail])

    def test_free_text_and_no_links_are_supported(self):
        response = self.client.post("/coverage-requests", json=self.payload | {"kind": "description", "input": "  Finnish robotics companies  ", "depth": 0})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["input"], "Finnish robotics companies")

    def test_limits_and_server_owned_status_cannot_be_bypassed(self):
        for change in [{"depth": 3}, {"depth": -1}, {"depth": True}, {"max_pages": 1001}, {"max_pages": 0}, {"max_pages": 1.5}, {"link_scope": "all"}, {"kind": "other"}, {"input": " "}, {"input": "x" * 4001}, {"input": "ftp://example.org"}, {"input": "https://user:pass@example.org"}, {"input": "not a URL"}, {"status": "ongoing"}, {"completed_at": "2026-09-06"}]:
            with self.subTest(change=change):
                response = self.client.post("/coverage-requests", json=self.payload | change)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/coverage-requests").json()["total"], 0)

    def test_list_pagination_and_recent_completion_filter(self):
        ids = [self.client.post("/coverage-requests", json=self.payload).json()["id"] for _ in range(4)]
        with Session(self.engine) as session, session.begin():
            ongoing = session.get(CoverageRequest, UUID(ids[0]))
            ongoing.status = "ongoing"
            for index, days in [(1, 1), (2, 31)]:
                row = session.get(CoverageRequest, UUID(ids[index]))
                row.status = "completed"
                row.completed_at = datetime.now(UTC) - timedelta(days=days)
        self.assertEqual(self.client.get("/coverage-requests?status=ongoing").json()["items"][0]["id"], ids[0])
        self.assertEqual(self.client.get("/coverage-requests?status=completed").json()["items"][0]["id"], ids[1])
        self.assertEqual(self.client.get("/coverage-requests?status=completed").json()["total"], 1)
        self.assertEqual(self.client.get(f"/coverage-requests/{ids[2]}").status_code, 200)
        self.client.post("/coverage-requests", json=self.payload)
        first = self.client.get("/coverage-requests?limit=1").json()
        second = self.client.get("/coverage-requests?limit=1&offset=1").json()
        self.assertEqual(first["total"], 2)
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])

    def test_allowed_sections_are_normalized_and_persisted(self):
        response = self.client.post("/coverage-requests", json=self.payload | {
            "input": "https://example.org/docs/page?q=1", "allowed_sections": ["https://EXAMPLE.org/docs/", "https://example.org/docs"]})
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertEqual(data["allowed_sections"], ["https://example.org/docs"])
        self.assertEqual(self.client.get(f'/coverage-requests/{data["id"]}').json()["allowed_sections"], data["allowed_sections"])
        for sections in [["https://other.org/docs"], ["https://example.org/docs?x=1"],
                         ["https://example.org/docs#x"], ["https://user:pass@example.org/docs"],
                         ["ftp://example.org/docs"], ["https://example.org/" + "x" * 1000],
                         ["https://example.org/"] * 11]:
            with self.subTest(sections=sections):
                self.assertEqual(self.client.post("/coverage-requests", json=self.payload | {"allowed_sections": sections}).status_code, 422)

    def test_invalid_reads_and_mutations(self):
        self.assertEqual(self.client.get(f"/coverage-requests/{uuid4()}").status_code, 404)
        for query in ["limit=101", "offset=-1", "status=anything"]:
            self.assertEqual(self.client.get(f"/coverage-requests?{query}").status_code, 422)
        self.assertEqual(self.client.patch(f"/coverage-requests/{uuid4()}", json={"status": "completed"}).status_code, 403)
        self.assertEqual(self.client.post("/crawls/", json={}).status_code, 403)


class CoverageProgressTests(unittest.TestCase):
    def test_acquired_pages_are_visible_while_links_are_pending(self):
        from periplus.crawl.control.coverage_requests.schemas import CoverageProgress
        progress = CoverageProgress(status="running", request_count=106,
            pending_request_count=106, acquisition_pending_count=48,
            failed_request_count=0, crawl_limit_reached=False).model_dump()
        self.assertEqual(progress["acquisition_settled_count"], 58)
        self.assertEqual(progress["navigation_pending_count"], 58)

    def test_failed_acquisitions_are_not_reported_as_link_work(self):
        from periplus.crawl.control.coverage_requests.schemas import CoverageProgress
        progress = CoverageProgress(status="running", request_count=123,
            pending_request_count=115, acquisition_pending_count=48,
            failed_request_count=5, crawl_limit_reached=False).model_dump()
        self.assertEqual(progress["acquisition_settled_count"], 75)
        self.assertEqual(progress["navigation_pending_count"], 67)
