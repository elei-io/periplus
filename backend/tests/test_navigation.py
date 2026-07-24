from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import pyarrow as pa
from sqlglot import exp, parse_one

from repository.objects.store import FileObjectStore
from repository.objects.html import RawHtmlRepository, identify_html
from runtime.graph_navigation import EdgeResultCache, EdgeUrlExecutor
from runtime.edge_sql import edge_uses_catalogue
from runtime.navigation import (
    build_edge_selection_package,
    build_navigation_package,
    delete_run_navigation,
    edge_selection_object_name,
    load_edge_selection_package,
    load_navigation_package,
    navigation_object_name,
    put_edge_selection_package,
    put_navigation_package,
)


class NavigationPackageTests(unittest.TestCase):
    def test_edge_sql_classifies_page_only_and_historical_joins(self) -> None:
        self.assertFalse(
            edge_uses_catalogue(
                "SELECT target_url AS url FROM edge.page_links "
                "WHERE crawl_id = $crawl_id LIMIT 10"
            )
        )
        self.assertTrue(
            edge_uses_catalogue(
                "SELECT p.target_url AS url FROM edge.page_links AS p "
                "JOIN crawls AS c USING (document_id) "
                "WHERE p.crawl_id = $crawl_id LIMIT 10"
            )
        )

    def test_package_is_verified_and_deleted_with_its_run(self) -> None:
        run_id = uuid4()
        document_id = "sha256:" + "a" * 64
        html = '<a href="/next" title="Next page"> Next </a>'
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {"ATLAS_NAVIGATION_MAX_PACKAGE_BYTES": "1048576"},
            ),
        ):
            store = FileObjectStore(Path(directory))
            payload, rows = build_navigation_package(
                html,
                document_id=document_id,
                page_url="https://example.com/start",
            )
            name = navigation_object_name(
                run_id, document_id, "https://example.com/start"
            )
            package = put_navigation_package(
                store, name=name, payload=payload, row_count=rows
            )

            self.assertEqual(load_navigation_package(store, package), payload)
            table = pa.ipc.open_file(pa.BufferReader(payload)).read_all()
            self.assertNotIn("crawl_id", table.schema.names)
            self.assertEqual(
                table.column("target_url").to_pylist(),
                ["https://example.com/next"],
            )
            self.assertEqual(table.column("raw_href").to_pylist(), ["/next"])
            self.assertEqual(table.column("element_index").to_pylist(), [3])
            self.assertNotIn("link_index", table.schema.names)
            self.assertNotIn("text", table.schema.names)
            self.assertNotIn("title", table.schema.names)
            self.assertNotIn("rel", table.schema.names)
            self.assertNotIn("target_attribute", table.schema.names)
            self.assertEqual(delete_run_navigation(store, run_id), 1)
            self.assertFalse(store.exists(name))

    def test_package_preserves_link_occurrences_and_navigation_semantics(self) -> None:
        html = (
            '<main><a href="#reviews" rel="next" target="_self">Reviews</a>'
            '<a href="#specs">Specs</a>'
            '<a href="?page=2">Next page</a>'
            '<a href="/other">Other</a></main>'
        )

        with patch.dict(
            os.environ,
            {"ATLAS_NAVIGATION_MAX_PACKAGE_BYTES": "1048576"},
        ):
            payload, rows = build_navigation_package(
                html,
                document_id="sha256:" + "a" * 64,
                page_url="https://example.com/products?page=1",
            )

        table = pa.ipc.open_file(pa.BufferReader(payload)).read_all()
        self.assertEqual(rows, 4)
        self.assertEqual(
            table.column("target_url").to_pylist(),
            [
                "https://example.com/products?page=1",
                "https://example.com/products?page=1",
                "https://example.com/products?page=2",
                "https://example.com/other",
            ],
        )
        self.assertEqual(
            table.column("target_fragment").to_pylist(),
            ["reviews", "specs", None, None],
        )
        self.assertEqual(
            table.column("target_query").to_pylist(),
            ["page=1", "page=1", "page=2", None],
        )
        self.assertEqual(
            table.column("relation_kind").to_pylist(),
            ["same_url", "same_url", "same_path", "same_origin"],
        )

    def test_nav_links_exposes_the_current_crawl_id(self) -> None:
        crawl_id = uuid4()
        run_id = uuid4()
        html = '<a href="/next">Next</a>'
        document_id = identify_html(html).document_id
        payload, _ = build_navigation_package(
            html,
            document_id=document_id,
            page_url="https://example.com/start",
        )

        with tempfile.TemporaryDirectory() as directory:
            store = FileObjectStore(Path(directory))
            package = put_navigation_package(
                store,
                name=navigation_object_name(
                    run_id, document_id, "https://example.com/start"
                ),
                payload=payload,
                row_count=1,
            )
            RawHtmlRepository(store).put(
                html,
                source_url="https://example.com/start",
                crawl_id=crawl_id,
                captured_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
                content_type="text/html",
            )
            store.delete(package.object_name)
            urls = EdgeUrlExecutor(store, package)(
                "SELECT target_url AS url FROM edge.page_links "
                "WHERE crawl_id = $crawl_id LIMIT 100",
                {
                    "crawl_id": crawl_id,
                    "_page_url": "https://example.com/start",
                    "_document_id": document_id,
                },
            )
            self.assertEqual(urls, ["https://example.com/next"])
            self.assertTrue(store.exists(package.object_name))

    def test_edge_sql_can_select_expanded_navigation_knowledge(self) -> None:
        crawl_id = uuid4()
        run_id = uuid4()
        html = (
            '<a href="#details">Details</a>'
            '<a href="?page=2">Next page</a>'
            '<a href="/other">Other</a>'
        )
        document_id = identify_html(html).document_id
        payload, row_count = build_navigation_package(
            html,
            document_id=document_id,
            page_url="https://example.com/products?page=1",
        )

        with tempfile.TemporaryDirectory() as directory:
            store = FileObjectStore(Path(directory))
            package = put_navigation_package(
                store,
                name=navigation_object_name(
                    run_id, document_id, "https://example.com/products?page=1"
                ),
                payload=payload,
                row_count=row_count,
            )
            urls = EdgeUrlExecutor(store, package)(
                "SELECT target_url AS url FROM edge.page_links "
                "WHERE crawl_id = $crawl_id "
                "AND relation_kind = 'same_path' LIMIT 100",
                {
                    "crawl_id": crawl_id,
                    "_page_url": "https://example.com/products?page=1",
                    "_document_id": document_id,
                },
            )

        self.assertEqual(urls, ["https://example.com/products?page=2"])

    def test_page_only_edge_never_opens_catalogue(self) -> None:
        crawl_id = uuid4()
        run_id = uuid4()
        html = '<a href="/next">Next</a>'
        document_id = identify_html(html).document_id
        payload, row_count = build_navigation_package(
            html,
            document_id=document_id,
            page_url="https://example.com/start",
        )

        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "runtime.graph_navigation.catalogue_from_env",
                side_effect=AssertionError(
                    "page-only edges must not open DuckLake"
                ),
            ),
        ):
            store = FileObjectStore(Path(directory))
            package = put_navigation_package(
                store,
                name=navigation_object_name(
                    run_id,
                    document_id,
                    "https://example.com/start",
                ),
                payload=payload,
                row_count=row_count,
            )
            urls = EdgeUrlExecutor(store, package)(
                "SELECT target_url AS url FROM edge.page_links "
                "WHERE crawl_id = $crawl_id LIMIT 10",
                {
                    "crawl_id": crawl_id,
                    "_page_url": "https://example.com/start",
                    "_document_id": document_id,
                },
            )

        self.assertEqual(urls, ["https://example.com/next"])

    def test_historical_sources_are_pinned_to_the_run_snapshot(self) -> None:
        class Connection:
            def __init__(self) -> None:
                self.statements: list[str] = []

            def execute(self, sql: str):
                self.statements.append(sql)
                return self

        class Config:
            alias = "atlas"
            schema = "main"

        class Catalogue:
            config = Config()
            connection = Connection()
            trusted_connection = connection

        executor = EdgeUrlExecutor(
            object(),
            object(),  # type: ignore[arg-type]
            catalogue_snapshot_id=73,
        )
        statement = parse_one(
            "SELECT p.target_url AS url "
            "FROM atlas.main.previous_pages AS h "
            "JOIN atlas.main.documents AS d USING (document_id) "
            "JOIN edge.page_links AS p USING (document_id) "
            "WHERE p.crawl_id = $crawl_id LIMIT 10",
            dialect="duckdb",
        )
        for source in statement.find_all(exp.Table):
            if (
                source.db.lower() == "edge"
                and source.name.lower() == "page_links"
            ):
                source.set("db", None)
                source.set(
                    "this",
                    exp.to_identifier("atlas_navigation_links"),
                )

        executor._pin_catalogue_sources(Catalogue(), statement)

        self.assertEqual(len(Catalogue.connection.statements), 2)
        self.assertTrue(
            all(
                "AT (VERSION => 73)" in sql
                for sql in Catalogue.connection.statements
            )
        )

    def test_edge_result_bounds_are_enforced_while_streaming(self) -> None:
        batch = pa.record_batch(
            [pa.array(["https://example.com/1", "https://example.com/2"])],
            names=["url"],
        )
        reader = pa.RecordBatchReader.from_batches(batch.schema, [batch])

        with (
            patch.dict(
                os.environ,
                {
                    "ATLAS_EDGE_MAX_OUTPUT_ROWS": "1",
                    "ATLAS_EDGE_MAX_OUTPUT_BYTES": "1048576",
                },
            ),
            self.assertRaisesRegex(ValueError, "row limit"),
        ):
            EdgeUrlExecutor._collect_urls(reader)

    def test_deferred_edge_reuses_its_bounded_query_result(self) -> None:
        crawl_id = uuid4()
        run_id = uuid4()
        html = '<a href="/next">Next</a>'
        document_id = identify_html(html).document_id
        payload, row_count = build_navigation_package(
            html,
            document_id=document_id,
            page_url="https://example.com/start",
        )

        with tempfile.TemporaryDirectory() as directory:
            store = FileObjectStore(Path(directory))
            package = put_navigation_package(
                store,
                name=navigation_object_name(
                    run_id, document_id, "https://example.com/start"
                ),
                payload=payload,
                row_count=row_count,
            )
            cache = EdgeResultCache(maximum_bytes=1024 * 1024)
            parameters = {
                "crawl_id": crawl_id,
                "_page_url": "https://example.com/start",
                "_document_id": document_id,
            }
            sql = (
                "SELECT target_url AS url FROM edge.page_links "
                "WHERE crawl_id = $crawl_id LIMIT 100"
            )
            first = EdgeUrlExecutor(
                store,
                package,
                result_cache=cache,
                cache_key="evaluation",
            )(sql, parameters)
            store.delete(package.object_name)
            second = EdgeUrlExecutor(
                store,
                package,
                result_cache=cache,
                cache_key="evaluation",
            )(sql, parameters)

        self.assertEqual(first, ["https://example.com/next"])
        self.assertEqual(second, first)

    def test_deferred_edge_selection_survives_process_cache_loss(self) -> None:
        run_id = uuid4()
        urls = ("https://example.com/one", "https://example.com/two")
        payload = build_edge_selection_package(urls)

        with tempfile.TemporaryDirectory() as directory:
            store = FileObjectStore(Path(directory))
            package = put_edge_selection_package(
                store,
                name=edge_selection_object_name(
                    run_id,
                    "evaluation",
                    "a" * 64,
                ),
                payload=payload,
                row_count=len(urls),
            )
            loaded = load_edge_selection_package(store, package)

        self.assertEqual(loaded, urls)

    def test_relation_kind_uses_origin_host_and_registrable_domain(self) -> None:
        html = (
            '<a href="http://www.example.co.uk/other">HTTP</a>'
            '<a href="https://shop.example.co.uk/other">Shop</a>'
            '<a href="https://outside.example/other">Outside</a>'
        )

        with patch.dict(
            os.environ,
            {"ATLAS_NAVIGATION_MAX_PACKAGE_BYTES": "1048576"},
        ):
            payload, _ = build_navigation_package(
                html,
                document_id="sha256:" + "a" * 64,
                page_url="https://www.example.co.uk/start",
            )

        table = pa.ipc.open_file(pa.BufferReader(payload)).read_all()
        self.assertEqual(
            table.column("relation_kind").to_pylist(),
            ["same_host", "same_site", "external"],
        )


if __name__ == "__main__":
    unittest.main()
