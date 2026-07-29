"""Direct read-only DuckLake connection."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Mapping, Self
from urllib.parse import urlsplit

import duckdb
import psycopg
from dotenv import dotenv_values

from atlas_sdk.errors import AtlasConnectionError, ConfigurationError

from ._common import (
    ExtensionMode,
    QueryProfile,
    load_atlas_extension,
    quote_identifier,
    quote_literal,
    validate_catalogue,
)

_LAKE_SLUG = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


def _required(values: Mapping[str, str | None], *names: str) -> str:
    for name in names:
        value = values.get(name)
        if value:
            return value
    raise ConfigurationError(
        f"missing direct DuckLake setting: {', '.join(names)}"
    )


@dataclass(frozen=True, slots=True)
class DuckConfig:
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
    def from_env(cls) -> Self:
        return cls.from_values(os.environ)

    @classmethod
    def from_env_file(cls, path: str | Path) -> Self:
        return cls.from_values(dotenv_values(Path(path)))

    @classmethod
    def from_values(cls, values: Mapping[str, str | None]) -> Self:
        endpoint = _required(values, "AWS_ENDPOINT_URL", "ENDPOINT")
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigurationError(
                "AWS_ENDPOINT_URL must be an absolute HTTP or HTTPS URL"
            )
        try:
            port = int(_required(values, "PORT"))
        except ValueError as exc:
            raise ConfigurationError("PORT must be an integer") from exc
        return cls(
            postgres_host=_required(values, "HOST"),
            postgres_port=port,
            postgres_database=_required(values, "DATABASE"),
            postgres_user=_required(values, "USERNAME"),
            postgres_password=_required(values, "PASSWORD"),
            s3_key_id=_required(
                values, "AWS_ACCESS_KEY_ID", "ACCESS_KEY_ID"
            ),
            s3_secret=_required(
                values, "AWS_SECRET_ACCESS_KEY", "SECRET_ACCESS_KEY"
            ),
            s3_endpoint=endpoint,
            s3_region=_required(values, "AWS_REGION", "REGION"),
            s3_bucket=_required(values, "BUCKET"),
        )

    @property
    def endpoint_host(self) -> str:
        return urlsplit(self.s3_endpoint).netloc

    @property
    def uses_ssl(self) -> bool:
        return urlsplit(self.s3_endpoint).scheme == "https"


@dataclass(frozen=True, slots=True)
class _Lake:
    slug: str
    metadata_schema: str
    data_path: str


def duck(
    config: DuckConfig | None = None,
    *,
    lake: str | None = None,
    alias: str | None = None,
    read_only: bool = True,
    profile: QueryProfile = "interactive",
    extension: ExtensionMode = "auto",
    extension_path: str | Path | None = None,
) -> duckdb.DuckDBPyConnection:
    """Attach a Basin DuckLake directly and return its DuckDB connection."""

    if config is None:
        env_file = os.getenv("ATLAS_DIRECT_ENV_FILE")
        config = (
            DuckConfig.from_env_file(env_file)
            if env_file
            else DuckConfig.from_env()
        )
    slug = lake or os.getenv("DUCKBASIN_LAKE", "atlas_test")
    if not _LAKE_SLUG.fullmatch(slug):
        raise ConfigurationError("lake must be a lowercase snake_case slug")
    resolved = _resolve_lake(config, slug)
    catalogue_alias = alias or slug
    atlas_path = extension_path or os.getenv("ATLAS_DUCKDB_EXTENSION_PATH")
    connection = duckdb.connect(
        ":memory:",
        config={
            "allow_unsigned_extensions": (
                "true" if atlas_path is not None else "false"
            )
        },
    )
    try:
        for dependency in ("postgres", "httpfs", "ducklake"):
            connection.load_extension(dependency)
        loaded = load_atlas_extension(
            connection, mode=extension, path=atlas_path
        )
        connection.execute(
            _attach_sql(
                config,
                resolved,
                catalogue_alias,
                read_only=read_only,
            )
        )
        validate_catalogue(
            connection,
            profile=profile,
            extension_loaded=loaded,
        )
        return connection
    except BaseException as exc:
        connection.close()
        if isinstance(exc, duckdb.Error):
            raise AtlasConnectionError(
                "Direct DuckLake connection could not be established"
            ) from None
        raise


def _resolve_lake(config: DuckConfig, slug: str) -> _Lake:
    try:
        with psycopg.connect(
            host=config.postgres_host,
            port=config.postgres_port,
            dbname=config.postgres_database,
            user=config.postgres_user,
            password=config.postgres_password,
            connect_timeout=5,
            application_name="atlas-python-sdk",
            options="-c default_transaction_read_only=on",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT slug, metadata_schema, data_path
                    FROM public.ducklake_ducklake
                    WHERE slug = %s
                      AND status = 'active'
                      AND deleted_at IS NULL
                    """,
                    (slug,),
                )
                rows = cursor.fetchall()
    except Exception as exc:
        raise AtlasConnectionError(
            "DuckLake metadata lookup failed"
        ) from exc
    if len(rows) != 1:
        raise AtlasConnectionError(
            f"active Basin DuckLake was not uniquely available: {slug}"
        )
    resolved_slug, metadata_schema, data_path = rows[0]
    prefix = f"s3://{config.s3_bucket}/"
    if not str(data_path).startswith(prefix):
        raise AtlasConnectionError(
            "DuckLake data path is outside the configured bucket"
        )
    return _Lake(
        slug=str(resolved_slug),
        metadata_schema=str(metadata_schema),
        data_path=str(data_path),
    )


def _attach_sql(
    config: DuckConfig,
    lake: _Lake,
    alias: str,
    *,
    read_only: bool,
) -> str:
    mode = " (READ_ONLY)" if read_only else ""
    return f"""
CREATE TEMPORARY SECRET basin_direct_pg (
    TYPE postgres,
    HOST {quote_literal(config.postgres_host)},
    PORT {config.postgres_port},
    DATABASE {quote_literal(config.postgres_database)},
    USER {quote_literal(config.postgres_user)},
    PASSWORD {quote_literal(config.postgres_password)}
);
CREATE TEMPORARY SECRET basin_direct_s3 (
    TYPE s3,
    PROVIDER config,
    KEY_ID {quote_literal(config.s3_key_id)},
    SECRET {quote_literal(config.s3_secret)},
    REGION {quote_literal(config.s3_region)},
    ENDPOINT {quote_literal(config.endpoint_host)},
    URL_STYLE 'path',
    USE_SSL {str(config.uses_ssl).lower()},
    SCOPE {quote_literal(lake.data_path)}
);
CREATE TEMPORARY SECRET atlas_direct_lake (
    TYPE ducklake,
    METADATA_PATH '',
    METADATA_SCHEMA {quote_literal(lake.metadata_schema)},
    DATA_PATH {quote_literal(lake.data_path)},
    METADATA_PARAMETERS MAP {{
        'TYPE': 'postgres',
        'SECRET': 'basin_direct_pg'
    }}
);
ATTACH 'ducklake:atlas_direct_lake'
AS {quote_identifier(alias)}{mode};
USE {quote_identifier(alias)};
""".strip()
