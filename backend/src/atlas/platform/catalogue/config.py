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
    cdc_extension_path: str

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
        return self._resolved_extension_path(
            self.extension_path,
            variable="ATLAS_DUCKDB_EXTENSION_PATH",
            label="Atlas DuckDB extension",
        )

    def resolved_cdc_extension_path(self) -> Path:
        if not self.cdc_extension_path.strip():
            raise CatalogueConfigError(
                "ATLAS_DUCKLAKE_CDC_EXTENSION_PATH must identify the "
                "DuckLake CDC extension"
            )
        return self._resolved_extension_path(
            self.cdc_extension_path,
            variable="ATLAS_DUCKLAKE_CDC_EXTENSION_PATH",
            label="DuckLake CDC extension",
        )

    @staticmethod
    def _resolved_extension_path(
        value: str,
        *,
        variable: str,
        label: str,
    ) -> Path:
        try:
            path = Path(value).expanduser().resolve(strict=True)
        except OSError as exc:
            raise CatalogueConfigError(
                f"{label} was not found at {variable}"
            ) from exc
        if not path.is_file():
            raise CatalogueConfigError(
                f"{variable} must identify a file"
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
        cdc_extension_path=os.environ.get(
            "ATLAS_DUCKLAKE_CDC_EXTENSION_PATH",
            "",
        ),
    )


def _validate_name(label: str, value: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise CatalogueConfigError(
            f"catalogue {label} must match {_SAFE_NAME.pattern!r}, got {value!r}"
        )
