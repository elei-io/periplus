"""Typed DuckLake configuration for the Atlas repository."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ducklake_client import (
    CatalogConfig,
    DiskStorage,
    DuckDBCatalog,
    DuckDBConfig,
    DuckLakeAttachConfig,
    PostgresCatalog,
    S3Storage,
    SqliteCatalog,
    StorageConfig,
)
from config import get_optional, get_path, get_str

from repository.catalogue.exceptions import CatalogueConfigError

_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / ".atlas" / "repository"


@dataclass(frozen=True)
class CatalogueConfig:
    """Connection configuration for Atlas's managed DuckLake catalogue."""

    catalog: CatalogConfig
    storage: StorageConfig
    alias: str = "atlas"
    schema: str = "main"
    duckdb: DuckDBConfig = field(default_factory=DuckDBConfig)
    attach: DuckLakeAttachConfig = field(
        default_factory=lambda: DuckLakeAttachConfig(data_inlining_row_limit=0)
    )

    def __post_init__(self) -> None:
        _validate_name("alias", self.alias)
        _validate_name("schema", self.schema)


def catalogue_config_from_env() -> CatalogueConfig:
    """Build catalogue configuration from Atlas environment variables."""

    root = get_path("ATLAS_CATALOGUE_ROOT")
    catalog = _catalog_from_env(root)
    storage_kind = get_str("ATLAS_REPOSITORY_STORAGE").lower()
    storage = _storage_from_env(root)
    override_data_path = _optional_bool("ATLAS_CATALOGUE_OVERRIDE_DATA_PATH")
    duckdb = DuckDBConfig(
        database=get_str("ATLAS_CATALOGUE_DUCKDB_DATABASE"),
        threads=_optional_int("ATLAS_CATALOGUE_DUCKDB_THREADS"),
        memory_limit=_optional("ATLAS_CATALOGUE_DUCKDB_MEMORY_LIMIT"),
        max_temp_directory_size=_optional("ATLAS_CATALOGUE_DUCKDB_MAX_TEMP_SIZE"),
        temp_directory=_optional("ATLAS_CATALOGUE_DUCKDB_TEMP_DIRECTORY"),
    )
    return CatalogueConfig(
        catalog=catalog,
        storage=storage,
        alias=get_str("ATLAS_CATALOGUE_ALIAS"),
        schema=get_str("ATLAS_CATALOGUE_SCHEMA"),
        duckdb=duckdb,
        attach=DuckLakeAttachConfig(
            data_inlining_row_limit=_nonnegative_int(
                "ATLAS_CATALOGUE_DATA_INLINING_ROW_LIMIT",
                default=0,
            ),
            override_data_path=(
                storage_kind == "disk"
                if override_data_path is None
                else override_data_path
            ),
        ),
    )


def _catalog_from_env(root: Path) -> CatalogConfig:
    kind = get_str("ATLAS_CATALOGUE_CATALOG").lower()
    if kind == "duckdb":
        path = Path(
            get_optional("ATLAS_CATALOGUE_CATALOG_PATH") or root / "catalog.ducklake"
        ).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return DuckDBCatalog(path)
    if kind == "sqlite":
        path = Path(
            get_optional("ATLAS_CATALOGUE_CATALOG_PATH") or root / "catalog.sqlite"
        ).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return SqliteCatalog(path)
    if kind == "postgres":
        dsn = _required("ATLAS_CATALOGUE_CATALOG_DSN")
        return PostgresCatalog(dsn)
    raise CatalogueConfigError(
        "ATLAS_CATALOGUE_CATALOG must be one of: duckdb, sqlite, postgres"
    )


def _storage_from_env(root: Path) -> StorageConfig:
    kind = get_str("ATLAS_REPOSITORY_STORAGE").lower()
    if kind == "disk":
        repository_root = Path(
            get_str("ATLAS_REPOSITORY_ROOT")
        ).expanduser()
        path = repository_root / "lake"
        path.mkdir(parents=True, exist_ok=True)
        return DiskStorage(path)
    if kind == "s3":
        repository_prefix = (get_optional("ATLAS_REPOSITORY_S3_PREFIX") or "").strip("/")
        lake_prefix = f"{repository_prefix}/lake" if repository_prefix else "lake"
        return S3Storage(
            bucket=_required("ATLAS_REPOSITORY_S3_BUCKET"),
            prefix=lake_prefix,
            endpoint=_optional("ATLAS_REPOSITORY_S3_ENDPOINT"),
            region=_optional("ATLAS_REPOSITORY_S3_REGION") or _optional("AWS_REGION"),
            key_id=_optional("ATLAS_REPOSITORY_S3_KEY_ID")
            or _optional("AWS_ACCESS_KEY_ID"),
            secret_access_key=_optional("ATLAS_REPOSITORY_S3_SECRET_ACCESS_KEY")
            or _optional("AWS_SECRET_ACCESS_KEY"),
            session_token=_optional("ATLAS_REPOSITORY_S3_SESSION_TOKEN")
            or _optional("AWS_SESSION_TOKEN"),
            url_style=_optional("ATLAS_REPOSITORY_S3_URL_STYLE"),
            use_ssl=_optional_bool("ATLAS_REPOSITORY_S3_USE_SSL"),
        )
    raise CatalogueConfigError("ATLAS_REPOSITORY_STORAGE must be one of: disk, s3")


def _validate_name(label: str, value: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise CatalogueConfigError(
            f"catalogue {label} must match {_SAFE_NAME.pattern!r}, got {value!r}"
        )


def _required(name: str) -> str:
    value = _optional(name)
    if value is None:
        raise CatalogueConfigError(f"{name} is required")
    return value


def _optional(name: str) -> str | None:
    return get_optional(name)


def _optional_int(name: str) -> int | None:
    value = _optional(name)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise CatalogueConfigError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise CatalogueConfigError(f"{name} must be greater than zero")
    return parsed


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


def _nonnegative_int(name: str, *, default: int) -> int:
    value = _optional(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise CatalogueConfigError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise CatalogueConfigError(f"{name} must be zero or greater")
    return parsed
