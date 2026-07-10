from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ducklake_client import ColumnDef, DiskStorage, DuckDBCatalog, S3Storage

from repository.ducklake import Catalogue, CatalogueConfig, CatalogueConfigError, catalogue_config_from_env
from repository.ducklake.schema import CRAWL_COLUMNS, expected_columns


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

    def test_postgres_is_the_default_catalogue_and_requires_a_dsn(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                CatalogueConfigError,
                "ATLAS_CATALOGUE_CATALOG_DSN is required",
            ):
                catalogue_config_from_env()


class CatalogueBootstrapTests(unittest.TestCase):
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
                    INSERT INTO atlas.main.elements
                    VALUES ('sha256:test', 0, NULL, 'a', NULL, MAP {'href': '/docs'}, 'Docs', NULL)
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


if __name__ == "__main__":
    unittest.main()
