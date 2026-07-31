"""Direct read-only Atlas DuckLake connection."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Mapping, Self
from urllib.parse import urlsplit

import duckdb
from dotenv import dotenv_values

from atlas_sdk.errors import AtlasConnectionError, ConfigurationError

from ._common import ExtensionMode, QueryProfile
from ._factory import DuckLakeConnectionFactory
from ._protocol import DuckLakeConnectionProtocol

_ALIAS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_S3_URL_STYLES = frozenset({"path", "vhost", "virtual"})


def _required(values: Mapping[str, str | None], name: str) -> str:
    value = values.get(name)
    if value:
        return value
    raise ConfigurationError(f"missing direct DuckLake setting: {name}")


@dataclass(frozen=True, slots=True)
class S3Config:
    endpoint: str | None
    region: str
    key_id: str | None
    secret_access_key: str | None
    session_token: str | None
    url_style: str | None
    use_ssl: bool | None


@dataclass(frozen=True, slots=True)
class DuckConfig:
    alias: str
    metadata_path: str
    data_path: str
    metadata_schema: str = "ducklake"
    extension_path: str | None = None
    s3: S3Config | None = None

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
        data_path = _required(values, "ATLAS_DUCKLAKE_DATA_PATH")
        return cls(
            alias=alias,
            metadata_path=_required(
                values,
                "ATLAS_DUCKLAKE_METADATA_PATH",
            ),
            data_path=data_path,
            metadata_schema=(
                values.get("ATLAS_DUCKLAKE_METADATA_SCHEMA") or "ducklake"
            ),
            extension_path=values.get("ATLAS_DUCKDB_EXTENSION_PATH"),
            s3=_s3_config(values, data_path),
        )


def duck(
    config: DuckConfig | None = None,
    *,
    alias: str | None = None,
    read_only: bool = True,
    profile: QueryProfile = "interactive",
    extension: ExtensionMode = "required",
    extension_path: str | Path | None = None,
    protocol: DuckLakeConnectionProtocol | None = None,
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
    try:
        return DuckLakeConnectionFactory(
            config,
            protocol=protocol,
        ).connect(
            alias=catalogue_alias,
            read_only=read_only,
            profile=profile,
            extension=extension,
            extension_path=atlas_path,
        )
    except BaseException as exc:
        if isinstance(exc, duckdb.Error):
            raise AtlasConnectionError(
                "Direct DuckLake connection could not be established"
            ) from None
        raise


def _s3_config(
    values: Mapping[str, str | None],
    data_path: str,
) -> S3Config | None:
    if urlsplit(data_path).scheme.lower() != "s3":
        return None
    key_id = _first(values, "ATLAS_DUCKLAKE_S3_KEY_ID", "AWS_ACCESS_KEY_ID")
    secret_access_key = _first(
        values,
        "ATLAS_DUCKLAKE_S3_SECRET_ACCESS_KEY",
        "AWS_SECRET_ACCESS_KEY",
    )
    if bool(key_id) != bool(secret_access_key):
        raise ConfigurationError(
            "S3 DuckLake storage requires both a key ID and secret access key"
        )
    session_token = _first(
        values,
        "ATLAS_DUCKLAKE_S3_SESSION_TOKEN",
        "AWS_SESSION_TOKEN",
    )
    if session_token is not None and key_id is None:
        raise ConfigurationError(
            "S3 DuckLake storage cannot use a session token without credentials"
        )
    url_style = _value(values, "ATLAS_DUCKLAKE_S3_URL_STYLE")
    if url_style is not None:
        url_style = url_style.lower()
        if url_style not in _S3_URL_STYLES:
            raise ConfigurationError(
                "ATLAS_DUCKLAKE_S3_URL_STYLE must be one of: "
                "path, vhost, virtual"
            )
    return S3Config(
        endpoint=_value(values, "ATLAS_DUCKLAKE_S3_ENDPOINT"),
        region=(
            _value(values, "ATLAS_DUCKLAKE_S3_REGION")
            or _value(values, "AWS_REGION")
            or "us-east-1"
        ),
        key_id=key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
        url_style=url_style,
        use_ssl=_optional_bool(values, "ATLAS_DUCKLAKE_S3_USE_SSL"),
    )


def _first(
    values: Mapping[str, str | None],
    *names: str,
) -> str | None:
    return next(
        (value for name in names if (value := _value(values, name))),
        None,
    )


def _value(
    values: Mapping[str, str | None],
    name: str,
) -> str | None:
    value = values.get(name)
    return value.strip() if value is not None and value.strip() else None


def _optional_bool(
    values: Mapping[str, str | None],
    name: str,
) -> bool | None:
    value = _value(values, name)
    if value is None:
        return None
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean")
