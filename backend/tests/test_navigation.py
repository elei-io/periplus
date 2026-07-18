from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import pyarrow as pa

from repository.objects.store import FileObjectStore
from repository.objects.html import RawHtmlRepository, identify_html
from runtime.graph_navigation import EdgeUrlExecutor
from runtime.edge_sql import edge_uses_catalogue
from runtime.navigation import (
    build_navigation_package,
    delete_run_navigation,
    load_navigation_package,
    navigation_object_name,
    put_navigation_package,
)


class NavigationPackageTests(unittest.TestCase):
    def test_edge_sql_classifies_page_only_and_historical_joins(self) -> None:
        self.assertFalse(
            edge_uses_catalogue(
                "SELECT url FROM page.links "
                "WHERE crawl_id = $crawl_id LIMIT 10"
            )
        )
        self.assertTrue(
            edge_uses_catalogue(
                "SELECT p.url FROM page.links AS p "
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
            self.assertEqual(table.column("url").to_pylist(), ["https://example.com/next"])
            self.assertEqual(delete_run_navigation(store, run_id), 1)
            self.assertFalse(store.exists(name))

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
            RawHtmlRepository(store).put(html)
            store.delete(package.object_name)
            urls = EdgeUrlExecutor(store, package)(
                "SELECT url FROM page.links WHERE crawl_id = $crawl_id",
                {
                    "crawl_id": crawl_id,
                    "_page_url": "https://example.com/start",
                    "_document_id": document_id,
                },
            )
            self.assertEqual(urls, ["https://example.com/next"])
            self.assertTrue(store.exists(package.object_name))


if __name__ == "__main__":
    unittest.main()
