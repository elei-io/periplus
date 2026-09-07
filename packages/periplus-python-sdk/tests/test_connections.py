from __future__ import annotations

import unittest
from unittest.mock import Mock, call, patch
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from periplus_sdk.conn._common import validate_catalogue
from periplus_sdk.conn._direct import DuckConfig, S3Config, duck
from periplus_sdk.conn._factory import DuckLakeConnectionFactory
from periplus_sdk.conn._protocol import (
    DuckLakeConnectionProtocol,
    connection_protocol,
)
from periplus_sdk.errors import (
    PeriplusConnectionError,
    ConfigurationError,
)


class ConnectionConfigurationTests(unittest.TestCase):
    def test_connection_uses_only_official_extensions(self) -> None:
        config = DuckConfig(
            alias="periplus",
            metadata_path="metadata.duckdb",
            data_path="/tmp/lake",
        )
        connection = Mock()
        connection.execute.return_value = connection
        connection.fetchall.return_value = [
            ("web", "observation"),
            ("web", "link_occurrence"),
            ("web", "collection"),
            ("web", "fulfillment"),
            ("web", "acquisition_reason"),
            ("content", "object"),
            ("content", "html_element"),
        ]
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "periplus_sdk.conn._direct.duckdb.connect",
                return_value=connection,
            ),
        ):
            result = duck(config)

        self.assertIs(result, connection)
        connection.load_extension.assert_any_call("ducklake")
        self.assertEqual(connection.load_extension.call_count, 1)

    def test_direct_config_uses_periplus_ducklake_contract(self) -> None:
        config = DuckConfig.from_values(
            {
                "PERIPLUS_DUCKLAKE_ALIAS": "periplus",
                "PERIPLUS_DUCKLAKE_METADATA_PATH": (
                    "postgres:dbname=periplus host=postgres"
                ),
                "PERIPLUS_DUCKLAKE_DATA_PATH": "s3://periplus/lake/",
            }
        )

        self.assertEqual(config.alias, "periplus")
        self.assertEqual(config.metadata_schema, "ducklake")

    def test_s3_config_selects_a_parameterized_protocol(self) -> None:
        config = DuckConfig.from_values(
            {
                "PERIPLUS_DUCKLAKE_ALIAS": "periplus",
                "PERIPLUS_DUCKLAKE_METADATA_PATH": "metadata.sqlite",
                "PERIPLUS_DUCKLAKE_DATA_PATH": "s3://periplus/",
                "PERIPLUS_DUCKLAKE_S3_ENDPOINT": "gateway:7070",
                "PERIPLUS_DUCKLAKE_S3_KEY_ID": "key",
                "PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY": "secret-value",
                "PERIPLUS_DUCKLAKE_S3_URL_STYLE": "path",
                "PERIPLUS_DUCKLAKE_S3_USE_SSL": "false",
            }
        )
        self.assertIsInstance(config.s3, S3Config)
        connection = Mock()

        connection_protocol(config).configure(connection)

        secret_call = connection.execute.call_args
        self.assertNotIn("secret-value", secret_call.args[0])
        self.assertEqual(secret_call.args[1][-1], "s3://periplus/")

    def test_connection_factory_accepts_an_injected_protocol(self) -> None:
        config = DuckConfig(
            alias="periplus",
            metadata_path="metadata.ducklake",
            data_path="/srv/lake",
        )
        protocol = Mock(spec=DuckLakeConnectionProtocol)

        factory = DuckLakeConnectionFactory(config, protocol=protocol)

        self.assertIs(factory.protocol, protocol)

    def test_direct_config_requires_a_valid_alias(self) -> None:
        with self.assertRaises(ConfigurationError):
            DuckConfig.from_values(
                {
                    "PERIPLUS_DUCKLAKE_ALIAS": "not-valid",
                    "PERIPLUS_DUCKLAKE_METADATA_PATH": "metadata.sqlite",
                    "PERIPLUS_DUCKLAKE_DATA_PATH": "/tmp/lake",
                }
            )

    def test_direct_duckdb_errors_do_not_expose_metadata_path(self) -> None:
        secret = "postgres:password=database-secret"
        config = DuckConfig(
            alias="periplus",
            metadata_path=secret,
            data_path="/tmp/lake",
        )
        connection = Mock()
        connection.load_extension.side_effect = duckdb.Error(secret)
        with patch(
            "periplus_sdk.conn._direct.duckdb.connect",
            return_value=connection,
        ):
            with self.assertRaises(PeriplusConnectionError) as raised:
                duck(config)

        self.assertNotIn("database-secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
