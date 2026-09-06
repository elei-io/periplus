from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from periplus.platform.catalogue.__main__ import main as catalogue_main
from periplus.platform.catalogue.config import (
    CatalogueConfig,
    catalogue_config_from_env,
)
from periplus.platform.catalogue.connection import DuckLakeConnectionFactory
from periplus.platform.catalogue.exceptions import CatalogueConfigError
from periplus.platform.catalogue.storage import (
    DuckLakeStorageProtocol,
    storage_protocol,
)


class CatalogueConfigTests(unittest.TestCase):
    def test_attachment_paths_are_required(self) -> None:
        for missing, environment in (
            (
                "PERIPLUS_DUCKLAKE_METADATA_PATH",
                {"PERIPLUS_DUCKLAKE_DATA_PATH": "/srv/lake"},
            ),
            (
                "PERIPLUS_DUCKLAKE_DATA_PATH",
                {"PERIPLUS_DUCKLAKE_METADATA_PATH": "metadata.sqlite"},
            ),
        ):
            with self.subTest(missing=missing):
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaisesRegex(CatalogueConfigError, missing):
                        catalogue_config_from_env()

    def test_standard_postgres_metadata_url_is_normalized_for_ducklake(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PERIPLUS_DUCKLAKE_METADATA_PATH": (
                    "postgresql://periplus:secret@postgres.example.test/periplus_lake"
                ),
                "PERIPLUS_DUCKLAKE_DATA_PATH": "/srv/lake",
            },
            clear=True,
        ):
            config = catalogue_config_from_env()

        self.assertEqual(
            config.metadata_path,
            "postgres:postgresql://periplus:secret@postgres.example.test/periplus_lake",
        )

    def test_ducklake_postgres_metadata_path_is_not_double_prefixed(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PERIPLUS_DUCKLAKE_METADATA_PATH": (
                    "postgres:dbname=periplus_lake host=postgres.example.test"
                ),
                "PERIPLUS_DUCKLAKE_DATA_PATH": "/srv/lake",
            },
            clear=True,
        ):
            config = catalogue_config_from_env()

        self.assertEqual(
            config.metadata_path,
            "postgres:dbname=periplus_lake host=postgres.example.test",
        )

    def test_cdc_extension_path_must_identify_a_file(self) -> None:
        config = CatalogueConfig(
            alias="periplus",
            metadata_path="metadata.duckdb",
            data_path="lake/",
            metadata_schema="ducklake",
            cdc_extension_path="/missing/ducklake_cdc.duckdb_extension",
        )

        with self.assertRaisesRegex(
            CatalogueConfigError,
            "was not found",
        ):
            config.resolved_cdc_extension_path()

    def test_cdc_extension_path_resolves_existing_file(self) -> None:
        with TemporaryDirectory() as directory:
            extension = Path(directory) / "ducklake_cdc.duckdb_extension"
            extension.touch()
            config = CatalogueConfig(
                alias="periplus",
                metadata_path="metadata.duckdb",
                data_path="lake/",
                metadata_schema="ducklake",
                cdc_extension_path=str(extension),
            )

            self.assertEqual(
                config.resolved_cdc_extension_path(),
                extension.resolve(),
            )

    def test_cdc_extension_path_is_required_when_loaded(self) -> None:
        config = CatalogueConfig(
            alias="periplus",
            metadata_path="metadata.duckdb",
            data_path="lake/",
            metadata_schema="ducklake",
            cdc_extension_path="",
        )

        with self.assertRaisesRegex(
            CatalogueConfigError,
            "PERIPLUS_DUCKLAKE_CDC_EXTENSION_PATH",
        ):
            config.resolved_cdc_extension_path()

    def test_s3_data_path_uses_scoped_parameterized_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PERIPLUS_DUCKLAKE_METADATA_PATH": "metadata.sqlite",
                "PERIPLUS_DUCKLAKE_DATA_PATH": "s3://periplus/",
                "PERIPLUS_DUCKLAKE_S3_ENDPOINT": "gateway:7070",
                "PERIPLUS_DUCKLAKE_S3_KEY_ID": "key",
                "PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY": "secret-value",
                "PERIPLUS_DUCKLAKE_S3_URL_STYLE": "path",
                "PERIPLUS_DUCKLAKE_S3_USE_SSL": "false",
            },
            clear=True,
        ):
            config = catalogue_config_from_env()

        self.assertIsNotNone(config.s3)
        connection = MagicMock()
        storage_protocol(config).configure_connection(connection)

        secret_call = connection.execute.call_args_list[-1]
        self.assertNotIn("secret-value", secret_call.args[0])
        self.assertIn("SCOPE ?", secret_call.args[0])
        self.assertEqual(secret_call.args[1][-1], "s3://periplus/")

    def test_filesystem_data_path_needs_no_storage_secret(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PERIPLUS_DUCKLAKE_METADATA_PATH": "metadata.sqlite",
                "PERIPLUS_DUCKLAKE_DATA_PATH": "/srv/periplus/lake/",
            },
            clear=True,
        ):
            config = catalogue_config_from_env()

        self.assertIsNone(config.s3)
        connection = MagicMock()
        storage_protocol(config).configure_connection(connection)
        connection.execute.assert_not_called()

    def test_s3_static_credentials_must_be_complete(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PERIPLUS_DUCKLAKE_METADATA_PATH": "metadata.sqlite",
                "PERIPLUS_DUCKLAKE_DATA_PATH": "s3://periplus/",
                "PERIPLUS_DUCKLAKE_S3_KEY_ID": "key",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                CatalogueConfigError,
                "both a key ID and secret",
            ):
                catalogue_config_from_env()

    def test_connection_factory_accepts_an_injected_protocol(self) -> None:
        config = CatalogueConfig(
            alias="periplus",
            metadata_path="metadata.ducklake",
            data_path="/srv/lake",
            metadata_schema="ducklake",
            cdc_extension_path="",
        )
        protocol = MagicMock(spec=DuckLakeStorageProtocol)

        factory = DuckLakeConnectionFactory(config, protocol=protocol)

        self.assertIs(factory.storage, protocol)

    @patch("periplus.platform.catalogue.__main__.catalogue_from_env")
    def test_check_uses_a_read_only_portable_host_attachment(
        self,
        catalogue_from_env,
    ) -> None:
        catalogue = MagicMock()
        catalogue_from_env.return_value.__enter__.return_value = catalogue

        self.assertEqual(catalogue_main(["check"]), 0)

        catalogue_from_env.assert_called_once_with(
            read_only=True,
            override_data_path=True,
        )
        catalogue.validate_schema.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
