"""Shared connection validation."""

from __future__ import annotations


import duckdb

from periplus_sdk.errors import CatalogueVersionError

PUBLIC_CATALOGUE_VERSION = "2.0.0"
PUBLIC_RELATIONS = frozenset(
    {
        ("web", "observation"),
        ("web", "link_occurrence"),
        ("web", "collection"),
        ("web", "fulfillment"),
        ("web", "acquisition_reason"),
        ("content", "object"),
        ("content", "html_element"),
    }
)


def validate_catalogue(connection: duckdb.DuckDBPyConnection) -> str:
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
