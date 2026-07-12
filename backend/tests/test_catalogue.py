from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from ducklake_client import (
    ColumnDef,
    DiskStorage,
    DuckDBCatalog,
    DuckLakeAttachConfig,
    PostgresCatalog,
    S3Storage,
)

from repository.catalogue import Catalogue, CatalogueConfig, CatalogueConfigError, catalogue_config_from_env
from repository.catalogue import CrawlRecord
from repository.catalogue.service import CatalogueService
from repository.catalogue.schema import CRAWL_COLUMNS, expected_columns
from dom import encode_html


class CatalogueConfigTests(unittest.TestCase):
    def test_default_configuration_uses_local_ducklake(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ,
                {
                    "ATLAS_CATALOGUE_ROOT": temp_dir,
                    "ATLAS_CATALOGUE_CATALOG": "duckdb",
                },
                clear=True,
            ):
                config = catalogue_config_from_env()

        self.assertIsInstance(config.catalog, DuckDBCatalog)
        self.assertIsInstance(config.storage, DiskStorage)
        self.assertEqual(config.alias, "atlas")
        self.assertEqual(config.schema, "main")
        self.assertEqual(config.attach.data_inlining_row_limit, 10)
        self.assertTrue(config.attach.override_data_path)

    def test_s3_configuration_maps_credentials_and_endpoint(self) -> None:
        environment = {
            "ATLAS_CATALOGUE_CATALOG": "duckdb",
            "ATLAS_REPOSITORY_STORAGE": "s3",
            "ATLAS_REPOSITORY_S3_BUCKET": "atlas-data",
            "ATLAS_REPOSITORY_S3_PREFIX": "/catalogue/",
            "ATLAS_REPOSITORY_S3_ENDPOINT": "http://minio:9000",
            "ATLAS_REPOSITORY_S3_REGION": "us-east-1",
            "ATLAS_REPOSITORY_S3_KEY_ID": "atlas",
            "ATLAS_REPOSITORY_S3_SECRET_ACCESS_KEY": "secret",
            "ATLAS_REPOSITORY_S3_URL_STYLE": "path",
            "ATLAS_REPOSITORY_S3_USE_SSL": "false",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            environment["ATLAS_CATALOGUE_ROOT"] = temp_dir
            with patch.dict(os.environ, environment, clear=True):
                config = catalogue_config_from_env()

        self.assertIsInstance(config.storage, S3Storage)
        self.assertEqual(config.storage.data_path(), "s3://atlas-data/catalogue/lake")
        self.assertEqual(config.storage.endpoint, "http://minio:9000")
        self.assertFalse(config.storage.use_ssl)
        self.assertFalse(config.attach.override_data_path)

    def test_invalid_storage_kind_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ATLAS_CATALOGUE_CATALOG": "duckdb",
                "ATLAS_REPOSITORY_STORAGE": "ftp",
            },
            clear=True,
        ):
            with self.assertRaises(CatalogueConfigError):
                catalogue_config_from_env()

    def test_postgres_catalogue_has_a_local_default_dsn(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = catalogue_config_from_env()

        self.assertIsInstance(config.catalog, PostgresCatalog)
        self.assertEqual(
            config.catalog.dsn,
            "host=127.0.0.1 port=5432 dbname=atlas_catalogue user=atlas password=atlas",
        )


class CatalogueBootstrapTests(unittest.TestCase):
    def test_current_connection_snapshot_and_commit_metadata_are_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                with catalogue.lake.transaction():
                    catalogue.connection.execute(
                        "INSERT INTO atlas.main.crawls ("
                        "crawl_id, document_id, graph_id, graph_run_id, graph_node_id, "
                        "crawl_request_id, source_crawl_id, source_edge_id, "
                        "requested_url, normalized_url, final_url, page_url, "
                        "url_scheme, url_host, url_port, url_registrable_domain, "
                        "url_path, url_query, captured_at, status_code, duration_ms, "
                        "input_json, input_hash, crawl_policy_id, crawl_policy_revision, "
                        "data_schema_id, query_schema_id, warnings_json, errors_json"
                        ") SELECT uuid(), NULL, uuid(), uuid(), uuid(), uuid(), NULL, NULL, 'https://x', "
                        "'https://x/', NULL, 'https://x/', 'https', 'x', 443, 'x', '/', "
                        "'', now(), NULL, NULL, '{}', 'hash', NULL, NULL, NULL, NULL, "
                        "'[]', '[\"expected failure\"]'"
                    )
                    catalogue.set_commit_message(
                        author="Atlas test",
                        message="Snapshot attribution",
                        extra={"operation": "test"},
                    )
                snapshot = catalogue.last_committed_snapshot()
                latest = catalogue.latest_snapshot()
                metadata = catalogue.connection.execute(
                    "SELECT author, commit_message, commit_extra_info "
                    "FROM atlas.snapshots() WHERE snapshot_id = ?",
                    [snapshot],
                ).fetchone()

        self.assertEqual(snapshot, latest)
        self.assertEqual(metadata[0], "Atlas test")
        self.assertEqual(metadata[1], "Snapshot attribution")
        self.assertEqual(metadata[2], '{"operation":"test"}')

    def test_small_file_compaction_waits_for_threshold_and_merges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
                attach=DuckLakeAttachConfig(data_inlining_row_limit=0),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                for index in range(4):
                    catalogue.lake.table.append(
                        "elements",
                        [
                            {
                                "document_id": f"sha256:{index}",
                                "element_index": 0,
                                "parent_index": None,
                                "subtree_end_index": 0,
                                "depth": 0,
                                "tag": "p",
                                "namespace_uri": None,
                                "attributes": {},
                                "text_direct": str(index),
                                "text_tail": "",
                            }
                        ],
                        schema_name="main",
                    )
                service = CatalogueService(catalogue)

                skipped = service.compact_small_files(
                    minimum_files=5,
                    maximum_input_file_bytes=1024 * 1024,
                    target_file_bytes=2 * 1024 * 1024,
                    maximum_compacted_files=2,
                )
                compacted = service.compact_small_files(
                    minimum_files=4,
                    maximum_input_file_bytes=1024 * 1024,
                    target_file_bytes=2 * 1024 * 1024,
                    maximum_compacted_files=2,
                )
                active_files = catalogue.connection.execute(
                    """
                    SELECT count(*)
                    FROM __ducklake_metadata_atlas.ducklake_data_file AS data_file
                    JOIN __ducklake_metadata_atlas.ducklake_table AS table_info
                      ON table_info.table_id = data_file.table_id
                    WHERE table_info.table_name = 'elements'
                      AND table_info.end_snapshot IS NULL
                      AND data_file.end_snapshot IS NULL
                    """
                ).fetchone()[0]

        self.assertEqual(skipped, [])
        self.assertEqual(len(compacted), 1)
        self.assertEqual(compacted[0].table_name, "elements")
        self.assertEqual(compacted[0].eligible_files, 4)
        self.assertEqual(compacted[0].files_processed, 4)
        self.assertEqual(compacted[0].files_created, 1)
        self.assertEqual(active_files, 1)

    def test_small_file_compaction_includes_materialized_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
                attach=DuckLakeAttachConfig(data_inlining_row_limit=0),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                catalogue.connection.execute(
                    "CREATE TABLE atlas.materialized.compact_me(value INTEGER)"
                )
                for index in range(4):
                    catalogue.connection.execute(
                        "INSERT INTO atlas.materialized.compact_me VALUES (?)",
                        [index],
                    )

                compacted = CatalogueService(catalogue).compact_small_files(
                    minimum_files=4,
                    maximum_input_file_bytes=1024 * 1024,
                    target_file_bytes=2 * 1024 * 1024,
                    maximum_compacted_files=2,
                )

        self.assertEqual(len(compacted), 1)
        self.assertEqual(compacted[0].schema_name, "materialized")
        self.assertEqual(compacted[0].table_name, "compact_me")
        self.assertEqual(compacted[0].files_processed, 4)
        self.assertEqual(compacted[0].files_created, 1)

    def test_resolve_url_uses_standard_reference_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                result = catalogue.connection.execute(
                    """
                    SELECT resolve_url(source, href)
                    FROM (VALUES
                        ('https://books.toscrape.com/catalogue/item/index.html',
                         '../category/books_1/index.html'),
                        ('https://example.com/a/b', '/root'),
                        ('https://example.com/a/b', '//cdn.example.com/image.jpg'),
                        ('https://example.com/a/b?old=1', '?new=2')
                    ) AS examples(source, href)
                    """
                ).fetchall()

        self.assertEqual(
            result,
            [
                ("https://books.toscrape.com/catalogue/category/books_1/index.html",),
                ("https://example.com/root",),
                ("https://cdn.example.com/image.jpg",),
                ("https://example.com/a/b?new=2",),
            ],
        )
    def test_bootstrap_migrates_v1_crawl_document_id_to_nullable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                with catalogue.lake.transaction():
                    catalogue.lake.schema.create("main")
                    legacy_columns = dict(CRAWL_COLUMNS)
                    legacy_columns["document_id"] = ColumnDef(
                        "VARCHAR", nullable=False
                    )
                    catalogue.lake.table.create(
                        "crawls",
                        schema_name="main",
                        **legacy_columns,
                    )

            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                info = catalogue.lake.table.info(
                    "crawls",
                    schema_name="main",
                    include_summary=False,
                    include_row_count=False,
                    include_snapshots=False,
                )

            self.assertTrue(
                next(column for column in info.columns if column.name == "document_id").nullable
            )

    def test_bootstrap_is_idempotent_and_attributes_are_queryable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                first_snapshot = catalogue.latest_snapshot()
                catalogue.bootstrap()

                catalogue.connection.execute(
                    """
                    INSERT INTO atlas.main.elements (
                        document_id, element_index, parent_index, subtree_end_index,
                        depth, tag, namespace_uri, attributes, text_direct, text_tail
                    ) VALUES (
                        'sha256:test', 0, NULL, 0, 0, 'a', NULL,
                        MAP {'href': '/docs'}, 'Docs', ''
                    )
                    """
                )
                href = catalogue.lake.sql_scalar(
                    """
                    SELECT attributes['href']
                    FROM atlas.main.elements
                    WHERE document_id = $document_id
                    """,
                    document_id="sha256:test",
                )

                tables = {
                    table.table_name
                    for table in catalogue.lake.table.list(schema_name="main")
                }
                elements = catalogue.lake.table.info(
                    "elements",
                    schema_name="main",
                    include_summary=False,
                    include_row_count=False,
                    include_snapshots=False,
                )

            self.assertEqual(tables, set(expected_columns()))
            self.assertEqual(href, "/docs")
            self.assertIsNotNone(first_snapshot)
            self.assertTrue(all(column.summary is None for column in elements.columns))
            self.assertEqual(
                next(
                    column.data_type
                    for column in elements.columns
                    if column.name == "attributes"
                ),
                "MAP(VARCHAR, VARCHAR)",
            )

    def test_dom_macros_are_persistent_and_follow_projected_dom_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            rows = encode_html(
                '<div id="example">This is <span data-label="a&amp;b">'
                "some <b>bold</b></span> text!<br>next"
                "<script>x < y</script></div>"
            )
            div = next(row for row in rows if row.tag == "div")
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                catalogue.lake.table.append(
                    "elements",
                    [
                        {"document_id": "sha256:test", **asdict(row)}
                        for row in rows
                    ],
                    schema_name="main",
                )

            with Catalogue(config) as catalogue:
                result = catalogue.connection.execute(
                    """
                    SELECT
                        atlas.main.text_content('sha256:test', $element_index),
                        atlas.main.readable_text('sha256:test', $element_index),
                        atlas.main.inner_html('sha256:test', $element_index),
                        atlas.main.get_attribute(MAP {'disabled': '', 'href': '/docs'}, 'HREF'),
                        atlas.main.has_attribute(MAP {'disabled': ''}, 'disabled'),
                        atlas.main.has_text(' \n\t'),
                        atlas.main.has_text(' useful '),
                        atlas.main.has_text(NULL)
                    """,
                    {"element_index": div.element_index},
                ).fetchone()
                row_results = catalogue.connection.execute(
                    """
                    SELECT tag, atlas.main.text_content(document_id, element_index)
                    FROM atlas.main.elements
                    WHERE document_id = 'sha256:test'
                      AND tag IN ('b', 'script')
                    ORDER BY element_index
                    """
                ).fetchall()

            self.assertEqual(
                result,
                (
                    "This is some bold text!nextx < y",
                    "This is some bold text! nextx < y",
                    'This is <span data-label="a&amp;b">some <b>bold</b></span> '
                    "text!<br>next<script>x < y</script>",
                    "/docs",
                    True,
                    False,
                    True,
                    False,
                ),
            )
            self.assertEqual(row_results, [("b", "bold"), ("script", "x < y")])

    def test_crawl_urls_are_decomposed_without_nullable_derived_fields(self) -> None:
        crawl = CrawlRecord(
            crawl_id=UUID(int=1),
            document_id=None,
            graph_id=UUID(int=2),
            graph_run_id=UUID(int=3),
            graph_node_id=UUID(int=4),
            crawl_request_id=UUID(int=5),
            source_crawl_id=UUID(int=6),
            source_edge_id=UUID(int=7),
            requested_url="https://docs.example.co.jp/start",
            normalized_url="https://docs.example.co.jp/start",
            final_url="https://www.example.co.jp:8443/guides/sql?q=ducklake",
            captured_at=datetime(2026, 7, 11, tzinfo=UTC),
            input_json={},
            input_hash="input:test",
            errors_json=[{"message": "failed"}],
        )

        self.assertEqual(crawl.page_url, crawl.final_url)
        self.assertEqual(crawl.url_scheme, "https")
        self.assertEqual(crawl.url_host, "www.example.co.jp")
        self.assertEqual(crawl.url_port, 8443)
        self.assertEqual(crawl.url_registrable_domain, "example.co.jp")
        self.assertEqual(crawl.url_path, "/guides/sql")
        self.assertEqual(crawl.url_query, "q=ducklake")
        for name in (
            "page_url",
            "url_scheme",
            "url_host",
            "url_port",
            "url_registrable_domain",
            "url_path",
            "url_query",
        ):
            self.assertFalse(CRAWL_COLUMNS[name].nullable)


if __name__ == "__main__":
    unittest.main()
