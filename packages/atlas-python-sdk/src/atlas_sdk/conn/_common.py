"""Shared connection validation."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import duckdb

from atlas_sdk.errors import CatalogueVersionError, ExtensionVersionError

ExtensionMode = Literal["auto", "required", "disabled"]
QueryProfile = Literal["interactive", "batch"]


def load_atlas_extension(
    connection: duckdb.DuckDBPyConnection,
    *,
    mode: ExtensionMode,
    path: str | Path | None,
) -> bool:
    if mode not in {"auto", "required", "disabled"}:
        raise ValueError(f"invalid Atlas extension mode: {mode!r}")
    if mode == "disabled":
        return False
    if path is None:
        if mode == "required":
            raise ExtensionVersionError(
                "Atlas extension is required but no extension path was supplied"
            )
        return False
    try:
        connection.load_extension(str(Path(path).expanduser().resolve(strict=True)))
    except Exception as exc:
        raise ExtensionVersionError(
            "Atlas extension could not be loaded"
        ) from exc
    return True


def validate_catalogue(
    connection: duckdb.DuckDBPyConnection,
    *,
    profile: QueryProfile,
    extension_loaded: bool,
) -> str:
    if profile not in {"interactive", "batch"}:
        raise ValueError(f"invalid Atlas query profile: {profile!r}")
    if extension_loaded:
        connection.execute(
            f"SET atlas_query_profile = {quote_literal(profile)}"
        )
    try:
        row = connection.execute(
            "SELECT web._catalogue_version()"
        ).fetchone()
    except Exception as exc:
        raise CatalogueVersionError(
            "attached lake does not expose the Atlas public catalogue"
        ) from exc
    if row is None or not isinstance(row[0], str) or not row[0]:
        raise CatalogueVersionError(
            "Atlas public catalogue returned an invalid version"
        )
    return row[0]


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def quote_identifier(value: str) -> str:
    if "\x00" in value:
        raise ValueError("DuckDB identifiers cannot contain NUL")
    return '"' + value.replace('"', '""') + '"'
