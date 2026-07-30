"""Direct read-only Atlas DuckLake connection."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Mapping, Self

import duckdb
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

_ALIAS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _required(values: Mapping[str, str | None], name: str) -> str:
    value = values.get(name)
    if value:
        return value
    raise ConfigurationError(f"missing direct DuckLake setting: {name}")


@dataclass(frozen=True, slots=True)
class DuckConfig:
    alias: str
    metadata_path: str
    data_path: str
    metadata_schema: str = "ducklake"
    extension_path: str | None = None

    @classmethod
    def from_env(cls) -> Self:
        return cls.from_values(os.environ)

    @classmethod
    def from_env_file(cls, path: str | Path) -> Self:
        return cls.from_values(dotenv_values(Path(path)))

    @classmethod
    def from_values(cls, values: Mapping[str, str | None]) -> Self:
        alias = _required(values, "ATLAS_DUCKLAKE_ALIAS")
        if not _ALIAS.fullmatch(alias):
            raise ConfigurationError(
                "ATLAS_DUCKLAKE_ALIAS must be a SQL identifier"
            )
        return cls(
            alias=alias,
            metadata_path=_required(
                values,
                "ATLAS_DUCKLAKE_METADATA_PATH",
            ),
            data_path=_required(values, "ATLAS_DUCKLAKE_DATA_PATH"),
            metadata_schema=(
                values.get("ATLAS_DUCKLAKE_METADATA_SCHEMA") or "ducklake"
            ),
            extension_path=values.get("ATLAS_DUCKDB_EXTENSION_PATH"),
        )


def duck(
    config: DuckConfig | None = None,
    *,
    alias: str | None = None,
    read_only: bool = True,
    profile: QueryProfile = "interactive",
    extension: ExtensionMode = "required",
    extension_path: str | Path | None = None,
) -> duckdb.DuckDBPyConnection:
    """Attach Atlas's configured DuckLake and return a DuckDB connection."""

    if config is None:
        env_file = os.getenv("ATLAS_DIRECT_ENV_FILE")
        config = (
            DuckConfig.from_env_file(env_file)
            if env_file
            else DuckConfig.from_env()
        )
    catalogue_alias = alias or config.alias
    if not _ALIAS.fullmatch(catalogue_alias):
        raise ConfigurationError("alias must be a SQL identifier")
    atlas_path = (
        extension_path
        or config.extension_path
        or os.getenv("ATLAS_DUCKDB_EXTENSION_PATH")
    )
    connection = duckdb.connect(
        ":memory:",
        config={
            "allow_unsigned_extensions": (
                "true" if atlas_path is not None else "false"
            )
        },
    )
    try:
        connection.load_extension("ducklake")
        if config.metadata_path.startswith("postgres:"):
            connection.load_extension("postgres")
        loaded = load_atlas_extension(
            connection,
            mode=extension,
            path=atlas_path,
        )
        mode = ", READ_ONLY" if read_only else ""
        connection.execute(
            "ATTACH "
            f"{quote_literal('ducklake:' + config.metadata_path)} "
            f"AS {quote_identifier(catalogue_alias)} "
            f"(DATA_PATH {quote_literal(config.data_path)}, "
            f"METADATA_SCHEMA {quote_literal(config.metadata_schema)}"
            ", OVERRIDE_DATA_PATH true"
            f"{mode})"
        )
        connection.execute(f"USE {quote_identifier(catalogue_alias)}")
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
