from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.direct_ducklake import (
    BasinDirectDuckLakeClient,
    BasinInfrastructure,
    _sql_identifier,
    _sql_string,
)


class DirectDuckLakeScriptTests(unittest.TestCase):
    def test_reads_basin_infrastructure_without_exposing_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env.extra"
            env_file.write_text(
                "\n".join(
                    (
                        "DATABASE=basin",
                        "HOST=postgres.example",
                        "PASSWORD=postgres-secret",
                        "PORT=5432",
                        "USERNAME=basin",
                        "AWS_ACCESS_KEY_ID=s3-key",
                        "AWS_ENDPOINT_URL=https://s3.example:9000",
                        "AWS_REGION=lab-1",
                        "AWS_SECRET_ACCESS_KEY=s3-secret",
                        "BUCKET=basin",
                    )
                ),
                encoding="utf-8",
            )

            config = BasinInfrastructure.from_env_file(env_file)

        self.assertEqual(config.postgres_host, "postgres.example")
        self.assertEqual(config.s3_endpoint_host, "s3.example:9000")
        self.assertTrue(config.s3_uses_ssl)
        self.assertNotIn("postgres-secret", repr(config))
        self.assertNotIn("s3-secret", repr(config))

    def test_resolve_lake_uses_read_only_postgres_session(self) -> None:
        cursor = _FakeCursor(
            [
                (
                    "f8dbc6ba-3798-4e0b-910c-a1e0c85aaf50",
                    "atlas_test",
                    "ducklake_f8dbc6ba37984e0b910ca1e0c85aaf50",
                    "s3://basin/ducklakes/f8dbc6ba-3798-4e0b-910c-a1e0c85aaf50/",
                )
            ]
        )
        postgres = _FakePostgres(cursor)
        client = BasinDirectDuckLakeClient(
            _infrastructure(),
            postgres_connect=postgres,
        )

        lake = client.resolve_lake("atlas_test")

        self.assertEqual(lake.slug, "atlas_test")
        self.assertEqual(
            lake.metadata_schema,
            "ducklake_f8dbc6ba37984e0b910ca1e0c85aaf50",
        )
        self.assertEqual(postgres.kwargs["application_name"], "atlas-extension-dev")
        self.assertIn("default_transaction_read_only=on", postgres.kwargs["options"])
        self.assertEqual(cursor.parameters, ("atlas_test",))

    def test_rejects_lake_outside_configured_bucket(self) -> None:
        cursor = _FakeCursor(
            [
                (
                    "lake-id",
                    "atlas_test",
                    "ducklake_schema",
                    "s3://other-bucket/ducklakes/lake-id/",
                )
            ]
        )
        client = BasinDirectDuckLakeClient(
            _infrastructure(),
            postgres_connect=_FakePostgres(cursor),
        )

        with self.assertRaisesRegex(ValueError, "outside configured bucket"):
            client.resolve_lake("atlas_test")

    def test_sql_quoting(self) -> None:
        self.assertEqual(_sql_string("it's"), "'it''s'")
        self.assertEqual(_sql_identifier('atlas"test'), '"atlas""test"')

    def test_interactive_shell_uses_private_init_file(self) -> None:
        cursor = _FakeCursor(
            [
                (
                    "lake-id",
                    "atlas_test",
                    "ducklake_schema",
                    "s3://basin/ducklakes/lake-id/",
                )
            ]
        )
        client = BasinDirectDuckLakeClient(
            _infrastructure(),
            postgres_connect=_FakePostgres(cursor),
        )
        captured: dict[str, object] = {}

        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "duckdb"
            cli.write_text("#!/bin/sh\n", encoding="utf-8")
            cli.chmod(0o700)

            def run(
                command: list[str],
                *,
                check: bool,
            ) -> _FakeProcess:
                init_file = Path(command[3])
                captured["command"] = command
                captured["check"] = check
                captured["mode"] = init_file.stat().st_mode & 0o777
                captured["sql"] = init_file.read_text(encoding="utf-8")
                return _FakeProcess(returncode=0)

            with patch(
                "scripts.direct_ducklake.subprocess.run",
                side_effect=run,
            ):
                returncode = client.open_shell(duckdb_cli=cli)

        self.assertEqual(returncode, 0)
        self.assertEqual(captured["mode"], 0o600)
        self.assertEqual(captured["check"], False)
        self.assertIn("-unsigned", captured["command"])
        self.assertIn("ATTACH 'ducklake:atlas_direct_lake'", captured["sql"])
        self.assertIn("(READ_ONLY)", captured["sql"])


def _infrastructure() -> BasinInfrastructure:
    return BasinInfrastructure(
        postgres_host="postgres.example",
        postgres_port=5432,
        postgres_database="basin",
        postgres_user="basin",
        postgres_password="postgres-secret",
        s3_key_id="s3-key",
        s3_secret="s3-secret",
        s3_endpoint="https://s3.example:9000",
        s3_region="lab-1",
        s3_bucket="basin",
    )


class _FakeCursor:
    def __init__(self, rows: list[tuple[str, str, str, str]]) -> None:
        self.rows = rows
        self.parameters: tuple[str, ...] | None = None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, _sql: str, parameters: tuple[str, ...]) -> None:
        self.parameters = parameters

    def fetchall(self) -> list[tuple[str, str, str, str]]:
        return self.rows


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self._cursor


class _FakePostgres:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.kwargs: dict[str, object] = {}

    def __call__(self, **kwargs: object) -> _FakeConnection:
        self.kwargs = kwargs
        return _FakeConnection(self._cursor)


class _FakeProcess:
    def __init__(self, *, returncode: int) -> None:
        self.returncode = returncode


if __name__ == "__main__":
    unittest.main()
