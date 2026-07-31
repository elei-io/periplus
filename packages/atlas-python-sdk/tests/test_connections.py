from __future__ import annotations

import unittest
from unittest.mock import Mock, call, patch
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from atlas_sdk.conn._common import load_atlas_extension, validate_catalogue
from atlas_sdk.conn._direct import DuckConfig, S3Config, duck
from atlas_sdk.conn._factory import DuckLakeConnectionFactory
from atlas_sdk.conn._protocol import (
    DuckLakeConnectionProtocol,
    connection_protocol,
)
from atlas_sdk.errors import (
    AtlasConnectionError,
    ConfigurationError,
    ExtensionVersionError,
)


class ConnectionConfigurationTests(unittest.TestCase):
    def test_extension_is_required_by_default(self) -> None:
        config = DuckConfig(
            alias="atlas",
            metadata_path="metadata.duckdb",
            data_path="/tmp/lake",
        )
        connection = Mock()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "atlas_sdk.conn._direct.duckdb.connect",
                return_value=connection,
            ),
            self.assertRaises(ExtensionVersionError),
        ):
            duck(config)

        connection.load_extension.assert_any_call("ducklake")
        connection.close.assert_called_once_with()

    def test_connection_modes_are_runtime_validated(self) -> None:
        with self.assertRaises(ValueError):
            load_atlas_extension(
                Mock(),
                mode="surprise",  # type: ignore[arg-type]
                path=None,
            )
        with self.assertRaises(ValueError):
            validate_catalogue(
                Mock(),
                profile="surprise",  # type: ignore[arg-type]
                extension_loaded=False,
            )

    def test_direct_config_uses_atlas_ducklake_contract(self) -> None:
        config = DuckConfig.from_values(
            {
                "ATLAS_DUCKLAKE_ALIAS": "atlas",
                "ATLAS_DUCKLAKE_METADATA_PATH": (
                    "postgres:dbname=atlas host=postgres"
                ),
                "ATLAS_DUCKLAKE_DATA_PATH": "s3://atlas/lake/",
                "ATLAS_DUCKDB_EXTENSION_PATH": "/opt/atlas/atlas.duckdb_extension",
            }
        )

        self.assertEqual(config.alias, "atlas")
        self.assertEqual(config.metadata_schema, "ducklake")
        self.assertEqual(
            config.extension_path,
            "/opt/atlas/atlas.duckdb_extension",
        )

    def test_s3_config_selects_a_parameterized_protocol(self) -> None:
        config = DuckConfig.from_values(
            {
                "ATLAS_DUCKLAKE_ALIAS": "atlas",
                "ATLAS_DUCKLAKE_METADATA_PATH": "metadata.sqlite",
                "ATLAS_DUCKLAKE_DATA_PATH": "s3://atlas/",
                "ATLAS_DUCKLAKE_S3_ENDPOINT": "gateway:7070",
                "ATLAS_DUCKLAKE_S3_KEY_ID": "key",
                "ATLAS_DUCKLAKE_S3_SECRET_ACCESS_KEY": "secret-value",
                "ATLAS_DUCKLAKE_S3_URL_STYLE": "path",
                "ATLAS_DUCKLAKE_S3_USE_SSL": "false",
            }
        )
        self.assertIsInstance(config.s3, S3Config)
        connection = Mock()

        connection_protocol(config).configure(connection)

        secret_call = connection.execute.call_args
        self.assertNotIn("secret-value", secret_call.args[0])
        self.assertEqual(secret_call.args[1][-1], "s3://atlas/")

    def test_connection_factory_accepts_an_injected_protocol(self) -> None:
        config = DuckConfig(
            alias="atlas",
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
                    "ATLAS_DUCKLAKE_ALIAS": "not-valid",
                    "ATLAS_DUCKLAKE_METADATA_PATH": "metadata.sqlite",
                    "ATLAS_DUCKLAKE_DATA_PATH": "/tmp/lake",
                }
            )

    def test_extension_loads_before_ducklake_attach(self) -> None:
        config = DuckConfig(
            alias="atlas",
            metadata_path="metadata.duckdb",
            data_path="/host/lake",
        )
        connection = Mock()
        connection.execute.return_value = connection
        connection.fetchone.return_value = ("atlas-v1",)
        with TemporaryDirectory() as directory:
            extension = Path(directory) / "atlas.duckdb_extension"
            extension.touch()
            with patch(
                "atlas_sdk.conn._direct.duckdb.connect",
                return_value=connection,
            ) as connect:
                result = duck(config, extension_path=extension)

        self.assertIs(result, connection)
        connect.assert_called_once_with(
            ":memory:",
            config={"allow_unsigned_extensions": "true"},
        )
        extension_call = connection.method_calls.index(
            call.load_extension(str(extension.resolve()))
        )
        attach_call = next(
            index
            for index, item in enumerate(connection.method_calls)
            if item[0] == "execute"
            and item.args
            and str(item.args[0]).startswith("ATTACH ")
        )
        self.assertLess(extension_call, attach_call)
        attach_sql = str(connection.method_calls[attach_call].args[0])
        self.assertIn("OVERRIDE_DATA_PATH true", attach_sql)
        self.assertIn("READ_ONLY", attach_sql)

    def test_direct_duckdb_errors_do_not_expose_metadata_path(self) -> None:
        secret = "postgres:password=database-secret"
        config = DuckConfig(
            alias="atlas",
            metadata_path=secret,
            data_path="/tmp/lake",
        )
        connection = Mock()
        connection.load_extension.side_effect = duckdb.Error(secret)
        with patch(
            "atlas_sdk.conn._direct.duckdb.connect",
            return_value=connection,
        ):
            with self.assertRaises(AtlasConnectionError) as raised:
                duck(config)

        self.assertNotIn("database-secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
