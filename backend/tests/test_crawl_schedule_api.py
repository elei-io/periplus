from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from atlas.crawl.api.schedules import resource_router, router
from atlas.crawl.control.crawl_graphs.models import CrawlGraph, CrawlGraphEdge, CrawlGraphNode
from atlas.crawl.control.crawl_graphs.schemas import (
    CrawlGraphCreate,
    CrawlGraphNodeCreate,
    CrawlGraphUpdate,
)
from atlas.crawl.control.crawl_graphs.service import (
    create_graph,
    create_node,
    update_graph,
)
from atlas.crawl.control.crawl_schedules.models import CrawlSchedule
from atlas.platform.postgres import Base
from atlas.platform.postgres.session import get_session


class CrawlScheduleApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            self.engine,
            tables=[
                CrawlGraph.__table__,
                CrawlGraphNode.__table__,
                CrawlGraphEdge.__table__,
                CrawlSchedule.__table__,
            ],
        )
        with Session(self.engine, expire_on_commit=False) as session:
            graph = create_graph(session, CrawlGraphCreate())
            graph = update_graph(
                session,
                graph.id,
                CrawlGraphUpdate(
                    slug="scheduled",
                    description=None,
                    root_node_id=None,
                ),
            )
            create_node(
                session,
                graph.id,
                CrawlGraphNodeCreate(name="root"),
            )
            self.graph_id = graph.id
            session.commit()
        app = FastAPI()
        app.include_router(router)
        app.include_router(resource_router)

        def session_override():
            with Session(self.engine, expire_on_commit=False) as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

        app.dependency_overrides[get_session] = session_override
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()

    def test_schedule_crud_pause_and_preview(self) -> None:
        created = self.client.post(
            f"/crawl-plans/{self.graph_id}/schedules",
            json={
                "name": "Hourly",
                "timing": {"kind": "interval", "seconds": 3600},
                "root_url": "https://example.com/",
                "overlap_policy": "skip",
                "misfire_policy": "skip",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        schedule_id = created.json()["id"]
        listing = self.client.get(
            f"/crawl-plans/{self.graph_id}/schedules"
        )
        self.assertEqual(listing.json()["total"], 1)

        resources = self.client.get("/crawl-schedules/")
        self.assertEqual(resources.status_code, 200, resources.text)
        self.assertEqual(resources.json()["total"], 1)
        self.assertEqual(resources.json()["items"][0]["id"], schedule_id)
        self.assertEqual(
            resources.json()["items"][0]["plan_slug"], "scheduled"
        )

        resource = self.client.get(f"/crawl-schedules/{schedule_id}")
        self.assertEqual(resource.status_code, 200, resource.text)
        self.assertEqual(resource.json()["plan_id"], str(self.graph_id))
        self.assertEqual(resource.json()["plan_slug"], "scheduled")

        paused = self.client.put(
            f"/crawl-plans/{self.graph_id}/schedules/{schedule_id}/enabled",
            json={"enabled": False},
        )
        self.assertEqual(paused.status_code, 200, paused.text)
        self.assertEqual(paused.json()["status"], "paused")
        self.assertIsNone(paused.json()["next_run_at"])

        preview = self.client.post(
            f"/crawl-plans/{self.graph_id}/schedules/preview",
            json={
                "timing": {
                    "kind": "cron",
                    "expression": "0 6 * * *",
                    "timezone": "Asia/Tokyo",
                },
                "count": 3,
            },
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(len(preview.json()["occurrences"]), 3)

        deleted = self.client.delete(
            f"/crawl-plans/{self.graph_id}/schedules/{schedule_id}"
        )
        self.assertEqual(deleted.status_code, 204)
        missing_resource = self.client.get(
            f"/crawl-schedules/{schedule_id}"
        )
        self.assertEqual(missing_resource.status_code, 404)

    def test_invalid_schedule_url_is_422(self) -> None:
        response = self.client.post(
            f"/crawl-plans/{self.graph_id}/schedules",
            json={
                "name": "Invalid",
                "timing": {"kind": "interval", "seconds": 3600},
                "root_url": "file:///tmp/page.html",
            },
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
