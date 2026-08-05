"""Shared connection validation."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import duckdb

from periplus_sdk.errors import CatalogueVersionError, ExtensionVersionError

ExtensionMode = Literal["auto", "required", "disabled"]
QueryProfile = Literal["interactive", "batch"]
PUBLIC_CATALOGUE_VERSION = "1.0.0"
PUBLIC_RELATIONS = frozenset(
    {
        ("web", "observation"),
        ("web", "link_occurrence"),
        ("content", "object"),
        ("content", "html_element"),
    }
)


def load_periplus_extension(
    connection: duckdb.DuckDBPyConnection,
    *,
    mode: ExtensionMode,
    path: str | Path | None,
) -> bool:
    if mode not in {"auto", "required", "disabled"}:
        raise ValueError(f"invalid Periplus extension mode: {mode!r}")
    if mode == "disabled":
        return False
    if path is None:
        if mode == "required":
            raise ExtensionVersionError(
                "Periplus extension is required but no extension path was supplied"
            )
        return False
    try:
        connection.load_extension(str(Path(path).expanduser().resolve(strict=True)))
    except Exception as exc:
        raise ExtensionVersionError(
            "Periplus extension could not be loaded"
        ) from exc
    return True


def validate_catalogue(
    connection: duckdb.DuckDBPyConnection,
    *,
    profile: QueryProfile,
    extension_loaded: bool,
) -> str:
    if profile not in {"interactive", "batch"}:
        raise ValueError(f"invalid Periplus query profile: {profile!r}")
    if extension_loaded:
        connection.execute(
            f"SET periplus_query_profile = {quote_literal(profile)}"
        )
    try:
        rows = connection.execute(
            "SELECT schema_name, view_name FROM duckdb_views() "
            "WHERE database_name = current_catalog() "
            "AND schema_name IN ('web', 'content')"
        ).fetchall()
    except Exception as exc:
        raise CatalogueVersionError(
            "attached lake does not expose the Periplus public catalogue"
        ) from exc
    actual = {(str(schema), str(name)) for schema, name in rows}
    if actual != PUBLIC_RELATIONS:
        raise CatalogueVersionError(
            "attached lake exposes an incompatible Periplus public catalogue"
        )
    return PUBLIC_CATALOGUE_VERSION


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def quote_identifier(value: str) -> str:
    if "\x00" in value:
        raise ValueError("DuckDB identifiers cannot contain NUL")
    return '"' + value.replace('"', '""') + '"'
