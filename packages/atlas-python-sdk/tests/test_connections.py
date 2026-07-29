from __future__ import annotations

import unittest
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import duckdb

from atlas_sdk.conn._common import load_atlas_extension, validate_catalogue
from atlas_sdk.conn._direct import DuckConfig, _Lake, _attach_sql, duck
from atlas_sdk.conn._quack import (
    QuackConfig,
    QuackCredentials,
    _Target,
    _horizontalize,
    _target,
    quack,
)
from atlas_sdk.errors import AtlasConnectionError, ConfigurationError


class _Response:
    def __init__(self, payload, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.is_error = status_code >= 400

    def json(self):
        return self._payload


class ConnectionConfigurationTests(unittest.TestCase):
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

    def test_direct_config_redacts_secrets(self) -> None:
        config = DuckConfig.from_values(
            {
                "HOST": "postgres",
                "PORT": "5432",
                "DATABASE": "basin",
                "USERNAME": "atlas",
                "PASSWORD": "database-secret",
                "AWS_ACCESS_KEY_ID": "key",
                "AWS_SECRET_ACCESS_KEY": "object-secret",
                "AWS_ENDPOINT_URL": "http://minio:9000",
                "AWS_REGION": "us-east-1",
                "BUCKET": "lake",
            }
        )

        rendered = repr(config)
        self.assertNotIn("database-secret", rendered)
        self.assertNotIn("object-secret", rendered)

    def test_direct_config_rejects_relative_endpoint(self) -> None:
        with self.assertRaises(ConfigurationError):
            DuckConfig.from_values(
                {
                    "HOST": "postgres",
                    "PORT": "5432",
                    "DATABASE": "basin",
                    "USERNAME": "atlas",
                    "PASSWORD": "secret",
                    "AWS_ACCESS_KEY_ID": "key",
                    "AWS_SECRET_ACCESS_KEY": "secret",
                    "AWS_ENDPOINT_URL": "minio:9000",
                    "AWS_REGION": "us-east-1",
                    "BUCKET": "lake",
                }
            )

    def test_direct_credentials_are_process_temporary(self) -> None:
        config = DuckConfig.from_values(
            {
                "HOST": "postgres",
                "PORT": "5432",
                "DATABASE": "basin",
                "USERNAME": "atlas",
                "PASSWORD": "database-secret",
                "AWS_ACCESS_KEY_ID": "key",
                "AWS_SECRET_ACCESS_KEY": "object-secret",
                "AWS_ENDPOINT_URL": "http://minio:9000",
                "AWS_REGION": "us-east-1",
                "BUCKET": "lake",
            }
        )
        sql = _attach_sql(
            config,
            _Lake(
                slug="atlas_test",
                metadata_schema="ducklake_atlas",
                data_path="s3://lake/atlas",
            ),
            "atlas_test",
            read_only=True,
        )

        self.assertEqual(sql.count("CREATE TEMPORARY SECRET"), 3)
        self.assertIn("AS \"atlas_test\" (READ_ONLY)", sql)
        self.assertNotIn("CREATE PERSISTENT SECRET", sql)

    def test_direct_duckdb_errors_do_not_expose_credentials(self) -> None:
        config = DuckConfig.from_values(
            {
                "HOST": "postgres",
                "PORT": "5432",
                "DATABASE": "basin",
                "USERNAME": "atlas",
                "PASSWORD": "database-secret",
                "AWS_ACCESS_KEY_ID": "key",
                "AWS_SECRET_ACCESS_KEY": "object-secret",
                "AWS_ENDPOINT_URL": "http://minio:9000",
                "AWS_REGION": "us-east-1",
                "BUCKET": "lake",
            }
        )
        connection = Mock()
        connection.load_extension.side_effect = duckdb.Error(
            "database-secret object-secret"
        )
        with (
            patch(
                "atlas_sdk.conn._direct._resolve_lake",
                return_value=_Lake(
                    slug="atlas_test",
                    metadata_schema="ducklake_atlas",
                    data_path="s3://lake/atlas",
                ),
            ),
            patch(
                "atlas_sdk.conn._direct.duckdb.connect",
                return_value=connection,
            ),
        ):
            with self.assertRaises(AtlasConnectionError) as raised:
                duck(config)

        self.assertNotIn("database-secret", str(raised.exception))
        self.assertNotIn("object-secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        connection.close.assert_called_once_with()

    def test_quack_target_is_scoped_to_selected_lake(self) -> None:
        lake_id = uuid4()
        client = Mock()
        client.get.side_effect = [
            _Response(
                [
                    {
                        "id": str(lake_id),
                        "slug": "atlas_test",
                        "connectable": True,
                    }
                ]
            ),
            _Response(
                {
                    "id": str(lake_id),
                    "catalog_alias": "atlas_test",
                    "quack_uri": f"quack:{lake_id.hex}.catalog",
                    "secret_scope": f"quack:{lake_id.hex}.",
                    "disable_ssl": False,
                }
            ),
        ]
        config = QuackConfig(
            endpoint="https://basin.example",
            lake="atlas_test",
            credentials=QuackCredentials(
                token_endpoint="https://auth.example/token",
                client_id="atlas",
                client_secret="secret",
            ),
        )

        target = _target(client, config, "access-token")
        horizontal = _horizontalize(target, "session")

        self.assertEqual(horizontal.lake_id, lake_id)
        self.assertTrue(horizontal.uri.startswith("quack:session-"))
        self.assertTrue(horizontal.scope.startswith("quack:session-"))

    def test_quack_rejects_noncanonical_target(self) -> None:
        lake_id = uuid4()
        client = Mock()
        client.get.side_effect = [
            _Response(
                [
                    {
                        "id": str(lake_id),
                        "slug": "atlas_test",
                        "connectable": True,
                    }
                ]
            ),
            _Response(
                {
                    "id": str(uuid4()),
                    "catalog_alias": "atlas_test",
                    "quack_uri": "not-a-quack-target",
                    "secret_scope": "not-a-quack-scope",
                    "disable_ssl": False,
                }
            ),
        ]
        config = QuackConfig(
            endpoint="https://basin.example",
            lake="atlas_test",
            credentials=QuackCredentials(
                token_endpoint="https://auth.example/token",
                client_id="atlas",
                client_secret="secret",
            ),
        )

        with self.assertRaises(AtlasConnectionError):
            _target(client, config, "access-token")

    def test_quack_duckdb_errors_do_not_expose_tokens(self) -> None:
        lake_id = uuid4()
        config = QuackConfig(
            endpoint="https://basin.example",
            lake="atlas_test",
            credentials=QuackCredentials(
                token_endpoint="https://auth.example/token",
                client_id="atlas",
                client_secret="client-secret",
            ),
        )
        connection = Mock()
        connection.execute.side_effect = duckdb.Error("access-secret")
        http_client = MagicMock()
        http_client.__enter__.return_value = Mock()
        with (
            patch(
                "atlas_sdk.conn._quack.httpx.Client",
                return_value=http_client,
            ),
            patch(
                "atlas_sdk.conn._quack._token",
                return_value="access-secret",
            ),
            patch(
                "atlas_sdk.conn._quack._target",
                return_value=_Target(
                    lake_id=lake_id,
                    alias="atlas_test",
                    uri=f"quack:{lake_id.hex}.catalog",
                    scope=f"quack:{lake_id.hex}.",
                    disable_ssl=False,
                ),
            ),
            patch(
                "atlas_sdk.conn._quack.duckdb.connect",
                return_value=connection,
            ),
        ):
            with self.assertRaises(AtlasConnectionError) as raised:
                quack(config)

        self.assertNotIn("access-secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
