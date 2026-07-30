"""Direct DuckLake connection configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from atlas.platform.catalogue.exceptions import CatalogueConfigError


_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class CatalogueConfig:
    """One direct DuckLake attachment."""

    alias: str
    metadata_path: str
    data_path: str
    metadata_schema: str
    extension_path: str

    def __post_init__(self) -> None:
        _validate_name("alias", self.alias)
        _validate_name("metadata schema", self.metadata_schema)
        if not self.metadata_path.strip():
            raise CatalogueConfigError("catalogue metadata path must not be empty")
        if not self.data_path.strip():
            raise CatalogueConfigError("catalogue data path must not be empty")
        if not self.extension_path.strip():
            raise CatalogueConfigError(
                "ATLAS_DUCKDB_EXTENSION_PATH must identify the Atlas extension"
            )

    def resolved_extension_path(self) -> Path:
        try:
            path = Path(self.extension_path).expanduser().resolve(strict=True)
        except OSError as exc:
            raise CatalogueConfigError(
                "Atlas DuckDB extension was not found at "
                "ATLAS_DUCKDB_EXTENSION_PATH"
            ) from exc
        if not path.is_file():
            raise CatalogueConfigError(
                "ATLAS_DUCKDB_EXTENSION_PATH must identify a file"
            )
        return path


def catalogue_config_from_env() -> CatalogueConfig:
    return CatalogueConfig(
        alias=os.environ.get("ATLAS_DUCKLAKE_ALIAS", "atlas"),
        metadata_path=os.environ.get(
            "ATLAS_DUCKLAKE_METADATA_PATH",
            "postgres:dbname=atlas host=postgres port=5432 user=atlas",
        ),
        data_path=os.environ.get(
            "ATLAS_DUCKLAKE_DATA_PATH",
            "/app/.atlas/lake/",
        ),
        metadata_schema=os.environ.get(
            "ATLAS_DUCKLAKE_METADATA_SCHEMA",
            "ducklake",
        ),
        extension_path=os.environ.get(
            "ATLAS_DUCKDB_EXTENSION_PATH",
            "",
        ),
    )


def _validate_name(label: str, value: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise CatalogueConfigError(
            f"catalogue {label} must match {_SAFE_NAME.pattern!r}, got {value!r}"
        )
