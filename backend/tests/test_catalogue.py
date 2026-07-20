from __future__ import annotations

import os
import hashlib
import io
import tempfile
import unittest
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin
from unittest.mock import patch
from uuid import UUID, uuid4

from ducklake_client import (
    ColumnDef,
    DiskStorage,
    DuckDBCatalog,
    DuckLakeAttachConfig,
    PostgresCatalog,
    S3Storage,
)

from tests.catalogue_helpers import seed_system_macros
from dom import encode_html
from repository.catalogue import Catalogue, CatalogueConfig, CatalogueConfigError, catalogue_config_from_env
from repository.catalogue import CrawlRecord, CrawlStepRecord
from repository.catalogue.service import CatalogueService
from repository.catalogue.schema import (
    CRAWL_COLUMNS,
    INTERNAL_SCHEMA,
    expected_columns,
    expected_internal_columns,
)
from repository import (
    ArtifactIdentity,
    FileObjectStore,
    RawArtifactRepository,
    RawHtmlRepository,
    RepositoryIngestor,
)

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
        self.assertEqual(config.attach.data_inlining_row_limit, 0)
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
        self.assertEqual(
            config.duckdb.config,
            {
                "pg_pool_acquire_mode": "wait",
                "pg_pool_max_connections": "4",
                "pg_pool_idle_timeout_millis": "5000",
                "pg_pool_max_lifetime_millis": "60000",
                "pg_pool_wait_timeout_millis": "10000",
                "pg_pool_enable_reaper_thread": True,
            },
        )


class CatalogueBootstrapTests(unittest.TestCase):
    def test_artifact_ingestion_is_queryable_by_domain_path_and_media_type(self) -> None:
        payload = b"%PDF-1.7\r\nAtlas"
        identity = ArtifactIdentity(
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalogue = Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            )
            catalogue.bootstrap()
            store = FileObjectStore(root / "objects")
            RawArtifactRepository(store).put(
                io.BytesIO(payload),
                identity=identity,
            )
            ingestor = RepositoryIngestor(
                html_repository=RawHtmlRepository(store),
                catalogue=catalogue,
                staging_root=root / "staging",
            )
            crawl = CrawlRecord(
                crawl_id=uuid4(),
                artifact_id=identity.artifact_id,
                graph_id=uuid4(),
                graph_run_id=uuid4(),
                graph_node_id=uuid4(),
                crawl_request_id=uuid4(),
                requested_url="https://reports.example.com/reports/atlas.pdf",
                normalized_url="https://reports.example.com/reports/atlas.pdf",
                final_url="https://reports.example.com/reports/atlas.pdf",
                captured_at=datetime(2026, 7, 14, tzinfo=UTC),
                status_code=200,
                response_media_type="application/pdf",
                response_filename="atlas.pdf",
                policy_config_hash="a" * 64,
                policy_config_json={},
                outcome="success",
            )
            prepared = ingestor.prepare_from_raw(crawl=crawl)
            result = ingestor.commit_prepared_batch([prepared])[0]
            stored = ingestor.catalogue_service.get_artifact(identity.artifact_id)
            unique_pdfs = catalogue.connection.execute(
                "SELECT count(DISTINCT c.artifact_id) "
                "FROM atlas.main.crawls c "
                "JOIN atlas.main.artifacts a USING (artifact_id) "
                "WHERE c.url_registrable_domain = 'example.com' "
                "AND c.url_path LIKE '/reports/%' "
                "AND c.response_media_type = 'application/pdf'"
            ).fetchone()[0]
            ingestor.close()

        self.assertEqual(result.artifact_id, identity.artifact_id)
        self.assertTrue(result.artifact_created)
        self.assertIsNotNone(stored)
        self.assertEqual(unique_pdfs, 1)

    def test_ingestion_persists_typed_outcome_and_document_projection(self) -> None:
        html = (
            '<html><body><div id="root">Atlas</div><a href="/docs">Docs</a>'
            '<button class="load-more">Load more</button><form><input></form>'
            + "<script></script>" * 12
            + "</body></html>"
        )
        digest = hashlib.sha256(html.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalogue = Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            )
            catalogue.bootstrap()
            ingestor = RepositoryIngestor(
                html_repository=RawHtmlRepository(FileObjectStore(root / "objects")),
                catalogue=catalogue,
                staging_root=root / "staging",
            )
            ingestor.store_raw(html)
            crawl = CrawlRecord(
                crawl_id=uuid4(),
                document_id=f"sha256:{digest}",
                graph_id=uuid4(),
                graph_run_id=uuid4(),
                graph_node_id=uuid4(),
                crawl_request_id=uuid4(),
                requested_url="https://example.com/",
                normalized_url="https://example.com/",
                final_url="https://example.com/",
                captured_at=datetime(2026, 7, 14, tzinfo=UTC),
                status_code=200,
                duration_ms=12,
                policy_config_hash="a" * 64,
                policy_config_json={},
                outcome="success",
            )
            step = CrawlStepRecord(
                crawl_id=crawl.crawl_id,
                attempt_number=1,
                step_ordinal=1,
                method="wait_dynamic",
                method_version=1,
                config_hash="b" * 64,
                config_json={
                    "enabled": True,
                    "maximum_wait_ms": 8_000,
                    "sample_interval_ms": 250,
                    "stable_samples": 3,
                },
                started_at=datetime(2026, 7, 14, tzinfo=UTC),
                duration_ms=750,
                iterations=3,
                stop_reason="stable",
                before_element_count=3,
                after_element_count=18,
                before_text_chars=0,
                after_text_chars=25,
                before_link_count=0,
                after_link_count=1,
                before_scroll_height=100,
                after_scroll_height=200,
            )
            prepared = ingestor.prepare_from_raw(
                crawl=crawl,
                crawl_steps=(step,),
            )
            ingestor.commit_prepared_batch([prepared])
            stored_crawl = ingestor.catalogue_service.get_crawl(crawl.crawl_id)
            stored_step = catalogue.connection.execute(
                "SELECT method, duration_ms, before_element_count, "
                "after_element_count FROM atlas._atlas.crawl_steps "
                "WHERE crawl_id = ?",
                [crawl.crawl_id],
            ).fetchone()
            document = ingestor.catalogue_service.get_document(crawl.document_id)
            catalogue.connection.execute(
                "UPDATE atlas.main.documents SET parser_version = 'stale' "
                "WHERE document_id = ?",
                [crawl.document_id],
            )
            rebuilt = ingestor.prepare_from_raw(
                crawl=crawl,
                crawl_steps=None,
            )
            self.assertTrue(rebuilt.replace_projection)
            ingestor.commit_prepared_batch([rebuilt])
            rebuilt_document = ingestor.catalogue_service.get_document(crawl.document_id)
            ingestor.close()

        self.assertEqual(stored_crawl, crawl)
        self.assertEqual(stored_step, ("wait_dynamic", 750, 3, 18))
        self.assertIsNotNone(document)
        assert document is not None
        self.assertGreater(document.element_count, 0)
        self.assertEqual(rebuilt_document, document)

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
                        "policy_config_hash, policy_config_json, crawl_policy_id, "
                        "outcome, failure_code, failure_stage, "
                        "failure_retryable, failure_detail, acquisition_attempts_json"
                        ") SELECT uuid(), NULL, uuid(), uuid(), uuid(), uuid(), NULL, NULL, 'https://x', "
                        "'https://x/', NULL, 'https://x/', 'https', 'x', 443, 'x', '/', "
                        "'', now(), NULL, NULL, repeat('a', 64), "
                        "'{}', NULL, 'failed', 'expected_failure', 'request', "
                        "false, 'expected failure', '[]'"
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
                catalogue.connection.execute(
                    "CALL atlas.set_option('data_inlining_row_limit', 0, "
                    "schema => 'main', table_name => 'elements')"
                )
                for index in range(4):
                    catalogue.lake.table.append(
                        "elements",
                        [
                            {
                                "document_id": "sha256:compaction-test",
                                "element_index": index,
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
                    "CREATE TABLE atlas._atlas_materializations.compact_me(value INTEGER)"
                )
                for index in range(4):
                    catalogue.connection.execute(
                        "INSERT INTO atlas._atlas_materializations.compact_me VALUES (?)",
                        [index],
                    )

                compacted = CatalogueService(catalogue).compact_small_files(
                    minimum_files=4,
                    maximum_input_file_bytes=1024 * 1024,
                    target_file_bytes=2 * 1024 * 1024,
                    maximum_compacted_files=2,
                )

        self.assertEqual(len(compacted), 1)
        self.assertEqual(compacted[0].schema_name, "_atlas_materializations")
        self.assertEqual(compacted[0].table_name, "compact_me")
        self.assertEqual(compacted[0].files_processed, 4)
        self.assertEqual(compacted[0].files_created, 1)

    def test_resolve_url_uses_standard_reference_resolution(self) -> None:
        base = "http://a/b/c/d;p?q"
        references = (
            "g:h", "g", "./g", "g/", "/g", "//g", "?y", "g?y", "#s",
            "g#s", "g?y#s", ";x", "g;x", "g;x?y#s", "", ".", "./",
            "..", "../", "../g", "../..", "../../", "../../g", "../../../g",
            "/./g", "/../g", "g/./h", "g/../h", "http:g", "x??y#z#q",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                seed_system_macros(catalogue)
                placeholders = ", ".join("(?, ?)" for _ in references)
                result = catalogue.connection.execute(
                    f"""
                    SELECT macros.resolve_url(source, href)
                    FROM (VALUES {placeholders}) AS examples(source, href)
                    """,
                    [value for reference in references for value in (base, reference)],
                ).fetchall()

        self.assertEqual(
            [row[0] for row in result],
            [urljoin(base, reference) for reference in references],
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
                internal_tables = {
                    table.table_name
                    for table in catalogue.lake.table.list(
                        schema_name=INTERNAL_SCHEMA
                    )
                }
                elements = catalogue.lake.table.info(
                    "elements",
                    schema_name="main",
                    include_summary=False,
                    include_row_count=False,
                    include_snapshots=False,
                )

            self.assertEqual(tables, set(expected_columns()))
            self.assertEqual(internal_tables, set(expected_internal_columns()))
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

    def test_bootstrap_rewrites_legacy_element_files_into_bucket_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                catalogue.connection.execute(
                    """
                    INSERT INTO atlas.main.elements VALUES
                    ('sha256:legacy', 0, NULL, 0, 0, 'p', NULL, MAP {}, 'text', '')
                    """
                )
                with catalogue.lake.transaction():
                    catalogue.connection.execute(
                        "CREATE TABLE atlas.main.elements_legacy AS "
                        "SELECT * FROM atlas.main.elements"
                    )
                    catalogue.connection.execute("DROP TABLE atlas.main.elements")
                    catalogue.connection.execute(
                        "ALTER TABLE atlas.main.elements_legacy RENAME TO elements"
                    )

                catalogue.bootstrap()
                row_count = catalogue.connection.execute(
                    "SELECT count(*) FROM atlas.main.elements"
                ).fetchone()[0]
                files = catalogue.connection.execute(
                    """
                    SELECT count(*), count(df.partition_id)
                    FROM __ducklake_metadata_atlas.ducklake_data_file AS df
                    JOIN __ducklake_metadata_atlas.ducklake_table AS t
                      ON t.table_id = df.table_id
                    JOIN __ducklake_metadata_atlas.ducklake_schema AS s
                      ON s.schema_id = t.schema_id
                    WHERE s.schema_name = 'main' AND t.table_name = 'elements'
                      AND s.end_snapshot IS NULL AND t.end_snapshot IS NULL
                      AND df.end_snapshot IS NULL
                    """
                ).fetchone()

            self.assertEqual(row_count, 1)
            self.assertGreater(files[0], 0)
            self.assertEqual(files[0], files[1])

    def test_bootstrap_rewrites_previous_element_bucket_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                catalogue.connection.execute(
                    """
                    INSERT INTO atlas.main.elements VALUES
                    ('sha256:legacy-bucket', 0, NULL, 0, 0, 'p', NULL,
                     MAP {}, 'text', '')
                    """
                )
                with catalogue.lake.transaction():
                    catalogue.connection.execute(
                        "CREATE TABLE atlas.main.elements_bucket16 AS "
                        "SELECT * FROM atlas.main.elements WHERE false"
                    )
                    catalogue.connection.execute(
                        "ALTER TABLE atlas.main.elements_bucket16 "
                        "SET PARTITIONED BY (bucket(16, document_id))"
                    )
                    catalogue.connection.execute(
                        "INSERT INTO atlas.main.elements_bucket16 "
                        "SELECT * FROM atlas.main.elements"
                    )
                    catalogue.connection.execute(
                        "DROP TABLE atlas.main.elements"
                    )
                    catalogue.connection.execute(
                        "ALTER TABLE atlas.main.elements_bucket16 "
                        "RENAME TO elements"
                    )

                catalogue.bootstrap()
                partitioning = catalogue.connection.execute(
                    """
                    SELECT pc.transform
                    FROM __ducklake_metadata_atlas.ducklake_partition_info AS pi
                    JOIN __ducklake_metadata_atlas.ducklake_partition_column AS pc
                      USING (partition_id, table_id)
                    JOIN __ducklake_metadata_atlas.ducklake_table AS t
                      USING (table_id)
                    JOIN __ducklake_metadata_atlas.ducklake_schema AS s
                      USING (schema_id)
                    WHERE s.schema_name = 'main'
                      AND t.table_name = 'elements'
                      AND s.end_snapshot IS NULL
                      AND t.end_snapshot IS NULL
                      AND pi.end_snapshot IS NULL
                    """
                ).fetchall()
                row_count = catalogue.connection.execute(
                    "SELECT count(*) FROM atlas.main.elements"
                ).fetchone()[0]

            self.assertEqual(partitioning, [("bucket(256)",)])
            self.assertEqual(row_count, 1)

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
                seed_system_macros(catalogue)
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
                        atlas.macros.text_content('sha256:test', $element_index),
                        atlas.macros.readable_text('sha256:test', $element_index),
                        atlas.macros.inner_html('sha256:test', $element_index),
                        atlas.macros.get_attribute(MAP {'disabled': '', 'href': '/docs'}, 'HREF'),
                        atlas.macros.has_attribute(MAP {'disabled': ''}, 'disabled'),
                        atlas.macros.has_text(' \n\t'),
                        atlas.macros.has_text(' useful '),
                        atlas.macros.has_text(NULL)
                    """,
                    {"element_index": div.element_index},
                ).fetchone()
                row_results = catalogue.connection.execute(
                    """
                    SELECT tag, atlas.macros.text_content(document_id, element_index)
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
            policy_config_json={},
            policy_config_hash="a" * 64,
            outcome="failed",
            failure_code="expected_failure",
            failure_stage="request",
            failure_retryable=False,
            failure_detail="failed",
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
