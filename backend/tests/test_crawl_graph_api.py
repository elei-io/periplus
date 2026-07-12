from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.routers.crawl_graphs import router
from control.crawl_graphs.models import CrawlGraph, CrawlGraphEdge, CrawlGraphNode
from db import Base
from db.session import get_session


class CrawlGraphApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(
            self.engine,
            tables=[CrawlGraph.__table__, CrawlGraphNode.__table__, CrawlGraphEdge.__table__],
        )
        app = FastAPI()
        app.include_router(router)

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

    def test_crud_graph_nodes_and_edges(self) -> None:
        graph = self.client.post(
            "/crawl-graphs/", json={"name": "Search", "description": None}
        )
        self.assertEqual(graph.status_code, 201)
        graph_id = graph.json()["id"]
        search = self.client.post(
            f"/crawl-graphs/{graph_id}/nodes",
            json={"name": "search", "description": None},
        )
        result = self.client.post(
            f"/crawl-graphs/{graph_id}/nodes",
            json={"name": "result", "description": None},
        )
        edge = self.client.post(
            f"/crawl-graphs/{graph_id}/edges",
            json={
                "name": "results",
                "description": None,
                "source_node_id": search.json()["id"],
                "target_node_id": result.json()["id"],
                "sql": "SELECT url FROM materialized.page_links WHERE crawl_id = $crawl_id LIMIT 10",
            },
        )
        self.assertEqual(edge.status_code, 201, edge.text)
        detail = self.client.get(f"/crawl-graphs/{graph_id}")
        self.assertEqual(len(detail.json()["nodes"]), 2)
        self.assertEqual(detail.json()["root_node_id"], search.json()["id"])
        self.assertEqual(len(detail.json()["edges"]), 1)
        changed_root = self.client.put(
            f"/crawl-graphs/{graph_id}",
            json={"name": "Search", "description": None, "root_node_id": result.json()["id"]},
        )
        self.assertEqual(changed_root.status_code, 200, changed_root.text)
        self.assertEqual(changed_root.json()["root_node_id"], result.json()["id"])
        listing = self.client.get("/crawl-graphs/").json()
        self.assertEqual(listing["total"], 1)
        positioned = self.client.put(
            f"/crawl-graphs/{graph_id}/nodes/{search.json()['id']}/position",
            json={"x": 123.5, "y": 456},
        )
        self.assertEqual(positioned.status_code, 200, positioned.text)
        self.assertEqual(positioned.json()["position_x"], 123.5)

    def test_invalid_edge_is_422(self) -> None:
        graph_id = self.client.post("/crawl-graphs/", json={"name": "Search"}).json()["id"]
        node_id = self.client.post(
            f"/crawl-graphs/{graph_id}/nodes",
            json={"name": "search"},
        ).json()["id"]
        response = self.client.post(
            f"/crawl-graphs/{graph_id}/edges",
            json={
                "name": "bad",
                "source_node_id": node_id,
                "target_node_id": node_id,
                "sql": "SELECT url FROM t",
            },
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
