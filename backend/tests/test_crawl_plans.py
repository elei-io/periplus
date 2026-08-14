from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from periplus.crawl.control.crawl_graphs.models import (
    CrawlGraph,
    CrawlGraphEdge,
    CrawlGraphNode,
)
from periplus.crawl.control.crawl_graphs.schemas import (
    CrawlGraphCreate,
    CrawlGraphEdgeCreate,
    CrawlGraphNodeCreate,
    CrawlGraphUpdate,
)
from periplus.crawl.control.crawl_graphs.service import (
    CrawlGraphValidationError,
    create_edge,
    create_graph,
    create_node,
    freeze_graph,
    update_graph,
)
from periplus.crawl.api.runs import (
    CrawlRelationScope,
    _builtin_plan_snapshot,
)
from periplus.crawl.runtime.graph_navigation import EdgeUrlExecutor
from periplus.crawl.runtime.graph_queue import request_identity
from periplus.crawl.runtime.navigation import (
    build_navigation_package,
    put_navigation_package,
)
from periplus.ingestion.objects.store import FileObjectStore
from periplus.platform.postgres import Base


class CrawlPlanDefinitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            self.engine,
            tables=[
                CrawlGraph.__table__,
                CrawlGraphNode.__table__,
                CrawlGraphEdge.__table__,
            ],
        )
        self.session = Session(self.engine, expire_on_commit=False)
        plan = create_graph(self.session, CrawlGraphCreate())
        plan = update_graph(
            self.session,
            plan.id,
            CrawlGraphUpdate(
                slug="wikipedia",
                description=None,
                root_node_id=None,
            ),
        )
        self.root = create_node(
            self.session,
            plan.id,
            CrawlGraphNodeCreate(name="root"),
        )
        self.article = create_node(
            self.session,
            plan.id,
            CrawlGraphNodeCreate(name="article"),
        )
        self.plan_id = plan.id

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_page_local_edge_is_frozen_for_execution(self) -> None:
        edge = create_edge(
            self.session,
            self.plan_id,
            CrawlGraphEdgeCreate(
                name="same-origin",
                source_node_id=self.root.id,
                target_node_id=self.article.id,
                sql=(
                    "select target_url as url from nav.links "
                    "where relation_scope in ('self', 'same_origin')"
                ),
            ),
        )

        snapshot = freeze_graph(self.session, self.plan_id)

        self.assertEqual(snapshot.edges[0].id, edge.id)
        self.assertIn("FROM nav.links", snapshot.edges[0].sql)
        self.assertNotIn("dedupe", edge.model_dump())

    def test_plan_slugs_are_generated_sequentially(self) -> None:
        first = create_graph(self.session, CrawlGraphCreate())
        second = create_graph(self.session, CrawlGraphCreate())

        self.assertEqual(first.slug, "plan-001")
        self.assertEqual(second.slug, "plan-002")

    def test_historical_relation_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            CrawlGraphValidationError, "current nav.links"
        ):
            create_edge(
                self.session,
                self.plan_id,
                CrawlGraphEdgeCreate(
                    name="history",
                    source_node_id=self.root.id,
                    target_node_id=self.article.id,
                    sql="SELECT content_sha256 AS url FROM material.html_elements",
                ),
            )

    def test_table_function_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            CrawlGraphValidationError, "table functions"
        ):
            create_edge(
                self.session,
                self.plan_id,
                CrawlGraphEdgeCreate(
                    name="filesystem",
                    source_node_id=self.root.id,
                    target_node_id=self.article.id,
                    sql=(
                        "SELECT filename AS url "
                        "FROM read_csv_auto('/tmp/urls.csv')"
                    ),
                ),
            )

    def test_process_environment_access_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            CrawlGraphValidationError, "environment variables"
        ):
            create_edge(
                self.session,
                self.plan_id,
                CrawlGraphEdgeCreate(
                    name="environment",
                    source_node_id=self.root.id,
                    target_node_id=self.article.id,
                    sql=(
                        "SELECT getenv('HOME') AS url "
                        "FROM nav.links LIMIT 1"
                    ),
                ),
            )

    def test_url_identity_is_always_plan_wide(self) -> None:
        run_id = uuid4()
        self.assertEqual(
            request_identity(run_id, "HTTPS://Example.com"),
            request_identity(run_id, "https://example.com/"),
        )


class PageLinkEdgeExecutionTests(unittest.TestCase):
    def test_executor_selects_from_current_navigation_package(self) -> None:
        html = """
        <html><body>
          <a href="/same">same origin</a>
          <a href="https://other.example/out">external</a>
        </body></html>
        """
        content_sha256 = f"sha256:{sha256(html.encode()).hexdigest()}"
        payload, row_count = build_navigation_package(
            html,
            content_sha256=content_sha256,
            page_url="https://example.com/root",
        )
        with tempfile.TemporaryDirectory() as directory:
            store = FileObjectStore(Path(directory))
            package = put_navigation_package(
                store,
                name="runtime/navigation/test.arrow",
                payload=payload,
                row_count=row_count,
            )
            executor = EdgeUrlExecutor(store, package)

            urls = executor(
                "SELECT target_url AS url FROM nav.links "
                "WHERE relation_scope IN ('self', 'same_origin')",
                {
                    "crawl_id": uuid4(),
                    "_page_url": "https://example.com/root",
                    "_content_sha256": content_sha256,
                },
            )

        self.assertEqual(urls, ["https://example.com/same"])


class BuiltinCrawlPlanTests(unittest.TestCase):
    def test_depth_is_a_finite_linear_plan(self) -> None:
        snapshot = _builtin_plan_snapshot(
            2, CrawlRelationScope.same_origin
        )

        self.assertEqual(len(snapshot.nodes), 3)
        self.assertEqual(len(snapshot.edges), 2)
        self.assertEqual(
            snapshot.edges[0].source_node_id, snapshot.root_node_id
        )
        self.assertEqual(
            snapshot.edges[0].target_node_id,
            snapshot.edges[1].source_node_id,
        )
        self.assertIn("'self', 'same_origin'", snapshot.edges[0].sql)

    def test_builtin_plan_identity_is_deterministic(self) -> None:
        first = _builtin_plan_snapshot(
            2, CrawlRelationScope.same_origin
        )
        second = _builtin_plan_snapshot(
            2, CrawlRelationScope.same_origin
        )
        wider = _builtin_plan_snapshot(2, CrawlRelationScope.same_site)

        self.assertEqual(first, second)
        self.assertNotEqual(first.graph_id, wider.graph_id)


if __name__ == "__main__":
    unittest.main()
