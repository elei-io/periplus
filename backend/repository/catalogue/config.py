"""Logical Atlas catalogue configuration for a DuckBasin-managed lake."""

from __future__ import annotations

from dataclasses import dataclass
import re

from config import get_str
from repository.catalogue.exceptions import CatalogueConfigError


_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class CatalogueConfig:
    """Only the logical namespace remains Atlas configuration."""

    alias: str
    schema: str = "main"

    def __post_init__(self) -> None:
        _validate_name("alias", self.alias)
        _validate_name("schema", self.schema)


def catalogue_config_from_env() -> CatalogueConfig:
    return CatalogueConfig(
        alias=get_str("DUCKBASIN_LAKE"),
        schema=get_str("ATLAS_CATALOGUE_SCHEMA"),
    )


def _validate_name(label: str, value: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise CatalogueConfigError(
            f"catalogue {label} must match {_SAFE_NAME.pattern!r}, got {value!r}"
        )
