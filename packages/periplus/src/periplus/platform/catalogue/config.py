"""Direct DuckLake connection configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from urllib.parse import urlsplit

from periplus.platform.catalogue.exceptions import CatalogueConfigError


_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_S3_URL_STYLES = frozenset({"path", "vhost", "virtual"})


@dataclass(frozen=True, slots=True)
class S3StorageConfig:
    """Credentials and transport for one S3-compatible DuckLake data path."""

    endpoint: str | None
    region: str
    key_id: str | None
    secret_access_key: str | None
    session_token: str | None
    url_style: str | None
    use_ssl: bool | None


@dataclass(frozen=True, slots=True)
class CatalogueConfig:
    """One direct DuckLake attachment."""

    alias: str
    metadata_path: str
    data_path: str
    metadata_schema: str
    s3: S3StorageConfig | None = None

    def __post_init__(self) -> None:
        _validate_name("alias", self.alias)
        _validate_name("metadata schema", self.metadata_schema)
        if not self.metadata_path.strip():
            raise CatalogueConfigError("catalogue metadata path must not be empty")
        if not self.data_path.strip():
            raise CatalogueConfigError("catalogue data path must not be empty")


def catalogue_config_from_env() -> CatalogueConfig:
    data_path = _required("PERIPLUS_DUCKLAKE_DATA_PATH")
    return CatalogueConfig(
        alias=os.environ.get("PERIPLUS_DUCKLAKE_ALIAS", "periplus"),
        metadata_path=_metadata_path(),
        data_path=data_path,
        metadata_schema=os.environ.get(
            "PERIPLUS_DUCKLAKE_METADATA_SCHEMA",
            "ducklake",
        ),
        s3=_s3_storage_config(data_path),
    )


def _metadata_path() -> str:
    value = _required("PERIPLUS_DUCKLAKE_METADATA_PATH")
    if value.startswith(("postgresql://", "postgres://")):
        return "postgres:" + value
    return value


def _validate_name(label: str, value: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise CatalogueConfigError(
            f"catalogue {label} must match {_SAFE_NAME.pattern!r}, got {value!r}"
        )


def _s3_storage_config(data_path: str) -> S3StorageConfig | None:
    if urlsplit(data_path).scheme.lower() != "s3":
        return None
    key_id = _first_optional(
        "PERIPLUS_DUCKLAKE_S3_KEY_ID",
        "AWS_ACCESS_KEY_ID",
    )
    secret_access_key = _first_optional(
        "PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY",
        "AWS_SECRET_ACCESS_KEY",
    )
    if bool(key_id) != bool(secret_access_key):
        raise CatalogueConfigError(
            "S3 DuckLake storage requires both a key ID and secret access key"
        )
    session_token = _first_optional(
        "PERIPLUS_DUCKLAKE_S3_SESSION_TOKEN",
        "AWS_SESSION_TOKEN",
    )
    if session_token is not None and key_id is None:
        raise CatalogueConfigError(
            "S3 DuckLake storage cannot use a session token without credentials"
        )
    url_style = _optional("PERIPLUS_DUCKLAKE_S3_URL_STYLE")
    if url_style is not None:
        url_style = url_style.lower()
        if url_style not in _S3_URL_STYLES:
            raise CatalogueConfigError(
                "PERIPLUS_DUCKLAKE_S3_URL_STYLE must be one of: "
                "path, vhost, virtual"
            )
    return S3StorageConfig(
        endpoint=_optional("PERIPLUS_DUCKLAKE_S3_ENDPOINT"),
        region=(
            _optional("PERIPLUS_DUCKLAKE_S3_REGION")
            or _optional("AWS_REGION")
            or "us-east-1"
        ),
        key_id=key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
        url_style=url_style,
        use_ssl=_optional_bool("PERIPLUS_DUCKLAKE_S3_USE_SSL"),
    )


def _first_optional(*names: str) -> str | None:
    return next((value for name in names if (value := _optional(name))), None)


def _required(name: str) -> str:
    value = _optional(name)
    if value is None:
        raise CatalogueConfigError(f"{name} is required")
    return value


def _optional(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value is not None and value.strip() else None


def _optional_bool(name: str) -> bool | None:
    value = _optional(name)
    if value is None:
        return None
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise CatalogueConfigError(f"{name} must be a boolean")
