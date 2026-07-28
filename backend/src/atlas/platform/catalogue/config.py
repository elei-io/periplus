"""Logical Atlas catalogue configuration for a DuckBasin-managed lake."""

from __future__ import annotations

from dataclasses import dataclass
import re

from atlas.platform.catalogue.exceptions import CatalogueConfigError


_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class CatalogueConfig:
    """The selected DuckBasin catalogue alias."""

    alias: str

    def __post_init__(self) -> None:
        _validate_name("alias", self.alias)


def catalogue_config_from_env(*, alias: str) -> CatalogueConfig:
    return CatalogueConfig(alias=alias)


def _validate_name(label: str, value: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise CatalogueConfigError(
            f"catalogue {label} must match {_SAFE_NAME.pattern!r}, got {value!r}"
        )
