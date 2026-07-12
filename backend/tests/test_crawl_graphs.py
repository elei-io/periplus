from __future__ import annotations

import unittest
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from control.crawl_graphs.models import CrawlGraph, CrawlGraphEdge, CrawlGraphNode
from control.crawl_graphs.schemas import (
    CrawlGraphCreate,
    CrawlGraphEdgeCreate,
    CrawlGraphEdgeUpdate,
    CrawlGraphNodeCreate,
    CrawlGraphNodePositionUpdate,
    CrawlGraphNodeUpdate,
)
from control.crawl_graphs.service import (
    CrawlGraphConflictError,
    CrawlGraphValidationError,
    create_edge,
    create_graph,
    create_node,
    delete_node,
    freeze_graph,
    update_edge,
    update_node,
    update_node_position,
    validate_edge_sql,
)
from db import Base


class CrawlGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            self.engine,
            tables=[CrawlGraph.__table__, CrawlGraphNode.__table__, CrawlGraphEdge.__table__],
        )
        self.session = Session(self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _graph_with_nodes(self):
        graph = create_graph(
            self.session, CrawlGraphCreate(name="Search", description="Search graph")
        )
        search = create_node(
            self.session,
            graph.id,
            CrawlGraphNodeCreate(name="search_page"),
        )
        result = create_node(
            self.session,
            graph.id,
            CrawlGraphNodeCreate(name="result_page"),
        )
        return graph, search, result

    def test_freeze_returns_complete_snapshot_and_makes_components_immutable(self) -> None:
        graph, search, result = self._graph_with_nodes()
        edge = create_edge(
            self.session,
            graph.id,
            CrawlGraphEdgeCreate(
                source_node_id=search.id,
                target_node_id=result.id,
                name="results",
                sql="SELECT url FROM materialized.page_links WHERE crawl_id = $crawl_id LIMIT 10",
            ),
        )

        snapshot = freeze_graph(self.session, graph.id)

        self.assertEqual(snapshot.graph_id, graph.id)
        self.assertEqual(snapshot.root_node_id, search.id)
        self.assertEqual({item.id for item in snapshot.nodes}, {search.id, result.id})
        self.assertEqual(snapshot.edges[0].id, edge.id)
        with self.assertRaises(CrawlGraphConflictError):
            update_node(
                self.session,
                graph.id,
                search.id,
                CrawlGraphNodeUpdate(name="changed"),
            )
        positioned = update_node_position(
            self.session,
            graph.id,
            search.id,
            CrawlGraphNodePositionUpdate(x=640.5, y=-20),
        )
        self.assertEqual((positioned.position_x, positioned.position_y), (640.5, -20))
        with self.assertRaises(CrawlGraphConflictError):
            update_edge(
                self.session,
                graph.id,
                edge.id,
                CrawlGraphEdgeUpdate(
                    source_node_id=search.id,
                    target_node_id=result.id,
                    name="changed",
                    sql=edge.sql,
                ),
            )

    def test_edge_endpoints_must_belong_to_graph(self) -> None:
        graph, search, _ = self._graph_with_nodes()
        other_graph = create_graph(self.session, CrawlGraphCreate(name="Other"))
        other = create_node(
            self.session,
            other_graph.id,
            CrawlGraphNodeCreate(name="other"),
        )
        with self.assertRaises(CrawlGraphValidationError):
            create_edge(
                self.session,
                graph.id,
                CrawlGraphEdgeCreate(
                    source_node_id=search.id,
                    target_node_id=other.id,
                    name="invalid",
                    sql="SELECT url FROM t WHERE crawl_id = $crawl_id LIMIT 1",
                ),
            )

    def test_deleting_node_deletes_incoming_and_outgoing_edges(self) -> None:
        graph, search, result = self._graph_with_nodes()
        create_edge(
            self.session,
            graph.id,
            CrawlGraphEdgeCreate(
                source_node_id=search.id,
                target_node_id=result.id,
                name="out",
                sql="SELECT url FROM t WHERE crawl_id = $crawl_id LIMIT 1",
            ),
        )
        create_edge(
            self.session,
            graph.id,
            CrawlGraphEdgeCreate(
                source_node_id=result.id,
                target_node_id=search.id,
                name="in",
                sql="SELECT url FROM t WHERE crawl_id = $crawl_id LIMIT 1",
            ),
        )
        delete_node(self.session, graph.id, search.id)
        self.assertEqual(self.session.query(CrawlGraphEdge).count(), 0)

    def test_edge_sql_contract(self) -> None:
        validate_edge_sql(
            "SELECT url FROM materialized.page_links WHERE crawl_id = $crawl_id LIMIT 10"
        )
        invalid = [
            "DELETE FROM materialized.page_links",
            "SELECT url FROM materialized.page_links LIMIT 10",
            "SELECT url FROM materialized.page_links WHERE crawl_id = $crawl_id",
            "SELECT host FROM materialized.page_links WHERE crawl_id = $crawl_id LIMIT 10",
            "SELECT url FROM t WHERE crawl_id = $crawl_id LIMIT 0",
            "SELECT url FROM t WHERE crawl_id = $crawl_id LIMIT 100001",
            "SELECT url FROM t WHERE crawl_id = $crawl_id; SELECT 1",
        ]
        for sql in invalid:
            with self.subTest(sql=sql), self.assertRaises(CrawlGraphValidationError):
                validate_edge_sql(sql)


if __name__ == "__main__":
    unittest.main()
