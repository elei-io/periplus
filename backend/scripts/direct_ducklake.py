"""Development-only direct connection to a Basin-managed DuckLake.

This module deliberately lives under ``backend/scripts`` rather than the Atlas
runtime package. Production Atlas processes use Quack and must not receive
DuckLake metadata or object-store credentials.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

import duckdb
import psycopg
from dotenv import dotenv_values


_LAKE_SLUG = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
_DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.extra"
_DEFAULT_DUCKDB_CLI = (
    Path(__file__).resolve().parents[3]
    / "atlas-duckdb-extension"
    / "build"
    / "release"
    / "duckdb"
)


def _required(
    values: Mapping[str, str | None],
    name: str,
    *fallbacks: str,
) -> str:
    for candidate in (name, *fallbacks):
        value = values.get(candidate)
        if value:
            return value
    choices = ", ".join((name, *fallbacks))
    raise ValueError(f"missing required direct-DuckLake setting: {choices}")


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_identifier(value: str) -> str:
    if "\x00" in value:
        raise ValueError("DuckDB identifiers cannot contain NUL")
    return '"' + value.replace('"', '""') + '"'


def _ducklake_attach_sql(
    config: BasinInfrastructure,
    lake: BasinLake,
    alias: str,
) -> str:
    return f"""
CREATE SECRET basin_direct_pg (
    TYPE postgres,
    HOST {_sql_string(config.postgres_host)},
    PORT {config.postgres_port},
    DATABASE {_sql_string(config.postgres_database)},
    USER {_sql_string(config.postgres_user)},
    PASSWORD {_sql_string(config.postgres_password)}
);
CREATE SECRET basin_direct_s3 (
    TYPE s3,
    PROVIDER config,
    KEY_ID {_sql_string(config.s3_key_id)},
    SECRET {_sql_string(config.s3_secret)},
    REGION {_sql_string(config.s3_region)},
    ENDPOINT {_sql_string(config.s3_endpoint_host)},
    URL_STYLE 'path',
    USE_SSL {str(config.s3_uses_ssl).lower()},
    SCOPE {_sql_string(lake.data_path)}
);
CREATE SECRET atlas_direct_lake (
    TYPE ducklake,
    METADATA_PATH '',
    METADATA_SCHEMA {_sql_string(lake.metadata_schema)},
    DATA_PATH {_sql_string(lake.data_path)},
    METADATA_PARAMETERS MAP {{
        'TYPE': 'postgres',
        'SECRET': 'basin_direct_pg'
    }}
);
ATTACH 'ducklake:atlas_direct_lake' AS {_sql_identifier(alias)} (READ_ONLY);
USE {_sql_identifier(alias)};
""".strip()


@dataclass(frozen=True, slots=True)
class BasinInfrastructure:
    postgres_host: str
    postgres_port: int
    postgres_database: str
    postgres_user: str
    postgres_password: str = field(repr=False)
    s3_key_id: str
    s3_secret: str = field(repr=False)
    s3_endpoint: str
    s3_region: str
    s3_bucket: str

    @classmethod
    def from_env_file(cls, path: Path) -> Self:
        values = dotenv_values(path)
        try:
            port = int(_required(values, "PORT"))
        except ValueError as exc:
            raise ValueError("PORT must be an integer") from exc
        endpoint_url = _required(values, "AWS_ENDPOINT_URL", "ENDPOINT")
        parsed = urlsplit(endpoint_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                "AWS_ENDPOINT_URL must be an absolute HTTP or HTTPS URL"
            )
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("AWS_ENDPOINT_URL must not include a path or query")
        return cls(
            postgres_host=_required(values, "HOST"),
            postgres_port=port,
            postgres_database=_required(values, "DATABASE"),
            postgres_user=_required(values, "USERNAME"),
            postgres_password=_required(values, "PASSWORD"),
            s3_key_id=_required(values, "AWS_ACCESS_KEY_ID", "ACCESS_KEY_ID"),
            s3_secret=_required(
                values,
                "AWS_SECRET_ACCESS_KEY",
                "SECRET_ACCESS_KEY",
            ),
            s3_endpoint=endpoint_url,
            s3_region=_required(values, "AWS_REGION", "REGION"),
            s3_bucket=_required(values, "BUCKET"),
        )

    @property
    def s3_endpoint_host(self) -> str:
        return urlsplit(self.s3_endpoint).netloc

    @property
    def s3_uses_ssl(self) -> bool:
        return urlsplit(self.s3_endpoint).scheme == "https"


@dataclass(frozen=True, slots=True)
class BasinLake:
    lake_id: str
    slug: str
    metadata_schema: str
    data_path: str


@dataclass(slots=True)
class DirectDuckLake:
    connection: duckdb.DuckDBPyConnection = field(repr=False)
    lake: BasinLake
    alias: str

    def execute(
        self,
        sql: str,
        parameters: Sequence[object] | Mapping[str, object] | None = None,
    ) -> duckdb.DuckDBPyConnection:
        if parameters is None:
            return self.connection.execute(sql)
        return self.connection.execute(sql, parameters)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class BasinDirectDuckLakeClient:
    """Resolve a Basin lake and attach it read-only to local DuckDB."""

    def __init__(
        self,
        infrastructure: BasinInfrastructure,
        *,
        postgres_connect: Callable[..., Any] = psycopg.connect,
        duckdb_connect: Callable[..., duckdb.DuckDBPyConnection] = duckdb.connect,
    ) -> None:
        self.infrastructure = infrastructure
        self._postgres_connect = postgres_connect
        self._duckdb_connect = duckdb_connect

    @classmethod
    def from_env_file(cls, path: Path = _DEFAULT_ENV_FILE) -> Self:
        return cls(BasinInfrastructure.from_env_file(path))

    def resolve_lake(self, slug: str) -> BasinLake:
        if not _LAKE_SLUG.fullmatch(slug):
            raise ValueError("lake slug must be lowercase snake_case")
        config = self.infrastructure
        with self._postgres_connect(
            host=config.postgres_host,
            port=config.postgres_port,
            dbname=config.postgres_database,
            user=config.postgres_user,
            password=config.postgres_password,
            connect_timeout=5,
            application_name="atlas-extension-dev",
            options="-c default_transaction_read_only=on",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id::text, slug, metadata_schema, data_path
                    FROM public.ducklake_ducklake
                    WHERE slug = %s
                      AND status = 'active'
                      AND deleted_at IS NULL
                    """,
                    (slug,),
                )
                rows = cursor.fetchall()
        if not rows:
            raise ValueError(f"active Basin DuckLake not found: {slug}")
        if len(rows) != 1:
            raise ValueError(f"Basin DuckLake slug is not unique: {slug}")
        lake_id, resolved_slug, metadata_schema, data_path = rows[0]
        expected_prefix = f"s3://{config.s3_bucket}/"
        if not str(data_path).startswith(expected_prefix):
            raise ValueError(
                f"lake data path is outside configured bucket {config.s3_bucket!r}"
            )
        return BasinLake(
            lake_id=str(lake_id),
            slug=str(resolved_slug),
            metadata_schema=str(metadata_schema),
            data_path=str(data_path),
        )

    def connect(
        self,
        slug: str = "atlas_test",
        *,
        alias: str | None = None,
        extension_path: Path | None = None,
    ) -> DirectDuckLake:
        lake = self.resolve_lake(slug)
        catalogue_alias = alias or slug
        config = self.infrastructure
        connection = self._duckdb_connect(
            ":memory:",
            config={
                "allow_unsigned_extensions": (
                    "true" if extension_path is not None else "false"
                )
            },
        )
        try:
            for extension in ("postgres", "httpfs", "ducklake"):
                connection.load_extension(extension)
            if extension_path is not None:
                resolved_extension = extension_path.expanduser().resolve(strict=True)
                connection.load_extension(str(resolved_extension))
            connection.execute(
                _ducklake_attach_sql(config, lake, catalogue_alias)
            )
        except BaseException:
            connection.close()
            raise
        return DirectDuckLake(
            connection=connection,
            lake=lake,
            alias=catalogue_alias,
        )

    def open_shell(
        self,
        slug: str = "atlas_test",
        *,
        alias: str | None = None,
        duckdb_cli: Path = _DEFAULT_DUCKDB_CLI,
    ) -> int:
        """Run DuckDB's native terminal with the direct lake attached."""

        resolved_cli = duckdb_cli.expanduser().resolve(strict=True)
        if not resolved_cli.is_file() or not os.access(resolved_cli, os.X_OK):
            raise ValueError(f"DuckDB CLI is not executable: {resolved_cli}")
        lake = self.resolve_lake(slug)
        catalogue_alias = alias or slug
        init_sql = "\n".join(
            (
                ".output /dev/null",
                "INSTALL postgres;",
                "INSTALL httpfs;",
                "INSTALL ducklake;",
                "LOAD postgres;",
                "LOAD httpfs;",
                "LOAD ducklake;",
                _ducklake_attach_sql(
                    self.infrastructure,
                    lake,
                    catalogue_alias,
                ),
                ".output",
            )
        )
        with tempfile.TemporaryDirectory(prefix="atlas-ducklake-") as directory:
            init_file = Path(directory) / "init.sql"
            init_file.write_text(init_sql, encoding="utf-8")
            init_file.chmod(0o600)
            process = subprocess.run(
                [
                    str(resolved_cli),
                    "-unsigned",
                    "-init",
                    str(init_file),
                    ":memory:",
                ],
                check=False,
            )
        return process.returncode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open a development-only direct Basin DuckLake connection."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=_DEFAULT_ENV_FILE,
        help="Basin infrastructure dotenv file (default: repository .env.extra)",
    )
    parser.add_argument("--lake", default="atlas_test")
    parser.add_argument("--alias")
    parser.add_argument(
        "--extension",
        type=Path,
        help="Unsigned local Atlas DuckDB extension binary to load",
    )
    parser.add_argument(
        "--duckdb-cli",
        type=Path,
        default=_DEFAULT_DUCKDB_CLI,
        help="Atlas-enabled DuckDB CLI used for interactive sessions",
    )
    parser.add_argument(
        "--sql",
        help="Run one SQL statement and exit; omit to open DuckDB's terminal",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    client = BasinDirectDuckLakeClient.from_env_file(arguments.env_file)
    if arguments.sql is None:
        return client.open_shell(
            arguments.lake,
            alias=arguments.alias,
            duckdb_cli=arguments.duckdb_cli,
        )
    with client.connect(
        arguments.lake,
        alias=arguments.alias,
        extension_path=arguments.extension,
    ) as lake:
        cursor = lake.execute(arguments.sql)
        columns = [str(column[0]) for column in cursor.description or ()]
        if columns:
            print("\t".join(columns))
        for row in cursor.fetchall():
            print("\t".join("NULL" if value is None else str(value) for value in row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
