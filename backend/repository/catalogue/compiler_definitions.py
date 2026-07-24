"""DuckLake-owned compiler definitions and a short-lived process cache."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from hashlib import sha256
import inspect
import time

from sqlglot import exp, parse_one

from atlas_sql import (
    CatalogueMetadataSnapshot,
    GraphEdgePurpose,
    CatalogueDefinitionPurpose,
    InteractiveQueryPurpose,
    ManagedTableMetadata,
    ScalarMacroDefinition,
    ScalarFunctionDefinition,
    TableMacroDefinition,
    ViewDefinition,
)
from repository.catalogue.quack_runtime import trusted_remote_rows


class CatalogueCompilerDefinitions(CatalogueMetadataSnapshot):
    """Loaded compiler snapshot plus purpose factories for repository callers."""

    def interactive_purpose(self) -> InteractiveQueryPurpose:
        return InteractiveQueryPurpose(metadata=self)

    def graph_edge_purpose(self) -> GraphEdgePurpose:
        return GraphEdgePurpose(
            scalar_macros=self.scalar_macros,
            table_macros=self.table_macros,
            views=self.views,
            scalar_functions=self.scalar_functions,
        )

    def definition_purpose(
        self,
        *,
        kind: str,
        schema_name: str,
        object_name: str,
        parameters: tuple[str, ...] = (),
    ) -> CatalogueDefinitionPurpose:
        return CatalogueDefinitionPurpose(
            kind=kind,  # type: ignore[arg-type]
            schema_name=schema_name,
            object_name=object_name,
            parameters=parameters,
            scalar_macros=self.scalar_macros,
            table_macros=self.table_macros,
            views=self.views,
            scalar_functions=self.scalar_functions,
        )


def read_catalogue_compiler_definitions(
    connection,
    *,
    catalogue_alias: str,
) -> CatalogueCompilerDefinitions:
    """Read one coherent definition snapshot from DuckDB catalogue metadata."""

    alias = _quote_literal(catalogue_alias)
    for _attempt in range(3):
        start_snapshot = _current_snapshot(connection, alias=alias)
        definitions = _read_catalogue_compiler_definitions_once(
            connection,
            catalogue_alias=catalogue_alias,
            snapshot_id=start_snapshot,
        )
        if _current_snapshot(connection, alias=alias) == start_snapshot:
            return definitions
    raise RuntimeError(
        "DuckLake catalogue metadata changed during three consecutive "
        "compiler snapshot reads."
    )


def _read_catalogue_compiler_definitions_once(
    connection,
    *,
    catalogue_alias: str,
    snapshot_id: int,
) -> CatalogueCompilerDefinitions:
    alias = _quote_literal(catalogue_alias)
    function_rows = trusted_remote_rows(
        connection,
        """
        SELECT schema_name, function_name, function_type, parameters,
               macro_definition
        FROM duckdb_functions()
        """
        f"WHERE database_name = {alias} "
        "AND function_type IN ('macro', 'table_macro') "
        "AND NOT internal "
        "ORDER BY schema_name, function_name, function_type",
    )
    view_rows = trusted_remote_rows(
        connection,
        """
        SELECT schema_name, view_name, sql
        FROM duckdb_views()
        """
        f"WHERE database_name = {alias} "
        "AND NOT internal "
        "ORDER BY schema_name, view_name",
    )
    scalar_function_rows = trusted_remote_rows(
        connection,
        """
        SELECT schema_name, function_name, has_side_effects, stability
        FROM duckdb_functions()
        WHERE function_type = 'scalar'
        ORDER BY schema_name, function_name, function_oid
        """,
    )
    table_rows = trusted_remote_rows(
        connection,
        """
        SELECT catalogue.schema_name, lake.table_name, lake.table_uuid,
               catalogue.estimated_size, lake.file_count,
               lake.file_size_bytes
        FROM ducklake_table_info("""
        f"{alias}"
        """) AS lake
        JOIN duckdb_tables() AS catalogue
          ON catalogue.database_name = """
        f"{alias}"
        """
         AND catalogue.table_name = lake.table_name
        QUALIFY count(*) OVER (PARTITION BY lake.table_name) = 1
        ORDER BY catalogue.schema_name, lake.table_name
        """,
    )
    scalar_macros = tuple(
        ScalarMacroDefinition(
            schema_name=str(row[0]),
            macro_name=str(row[1]),
            parameters=tuple(str(value) for value in (row[3] or ())),
            sql=str(row[4]),
        )
        for row in function_rows
        if str(row[2]) == "macro" and row[4] is not None
    )
    table_macros = tuple(
        TableMacroDefinition(
            schema_name=str(row[0]),
            macro_name=str(row[1]),
            parameters=tuple(str(value) for value in (row[3] or ())),
            # DuckDB 1.5 exposes the body and parameter names but not default
            # expressions. Explicit-argument calls remain fully optimizable.
            parameter_defaults=(),
            sql=str(row[4]),
        )
        for row in function_rows
        if str(row[2]) == "table_macro" and row[4] is not None
    )
    views = tuple(
        ViewDefinition(
            schema_name=str(row[0]),
            view_name=str(row[1]),
            sql=_view_query(str(row[2])),
        )
        for row in view_rows
        if row[2] is not None
    )
    scalar_functions = tuple(
        ScalarFunctionDefinition(
            schema_name=str(row[0]),
            function_name=str(row[1]),
            has_side_effects=bool(row[2]),
            stability=str(row[3]) if row[3] is not None else None,
        )
        for row in scalar_function_rows
    )
    tables = tuple(
        ManagedTableMetadata(
            schema_name=str(row[0]),
            table_name=str(row[1]),
            table_uuid=str(row[2]),
            estimated_rows=int(row[3]) if row[3] is not None else None,
            file_count=int(row[4]) if row[4] is not None else None,
            file_size_bytes=int(row[5]) if row[5] is not None else None,
        )
        for row in table_rows
    )
    fingerprint = sha256()
    fingerprint.update(str(snapshot_id).encode())
    fingerprint.update(b"\0")
    for definition in (
        *scalar_macros,
        *table_macros,
        *views,
        *scalar_functions,
        *tables,
    ):
        fingerprint.update(repr(definition).encode())
        fingerprint.update(b"\0")
    return CatalogueCompilerDefinitions(
        revision=fingerprint.hexdigest()[:16],
        scalar_macros=scalar_macros,
        table_macros=table_macros,
        views=views,
        scalar_functions=scalar_functions,
        tables=tables,
    )


def _current_snapshot(connection, *, alias: str) -> int:
    rows = trusted_remote_rows(
        connection,
        f"SELECT id FROM ducklake_current_snapshot({alias})",
    )
    if len(rows) != 1:
        raise RuntimeError(
            "DuckLake did not return exactly one current snapshot ID."
        )
    return int(rows[0][0])


DefinitionLoader = Callable[
    [],
    CatalogueCompilerDefinitions | Awaitable[CatalogueCompilerDefinitions],
]


class CatalogueCompilerDefinitionCache:
    def __init__(
        self,
        load: DefinitionLoader,
        *,
        ttl_seconds: float = 60.0,
    ) -> None:
        self._load = load
        self._ttl_seconds = ttl_seconds
        self._snapshot: CatalogueCompilerDefinitions | None = None
        self._expires_at = 0.0
        self._generation = 0
        self._lock = asyncio.Lock()

    async def get(self) -> CatalogueCompilerDefinitions:
        while True:
            now = time.monotonic()
            if self._snapshot is not None and now < self._expires_at:
                return self._snapshot
            async with self._lock:
                now = time.monotonic()
                if self._snapshot is not None and now < self._expires_at:
                    return self._snapshot
                generation = self._generation
                loaded = self._load()
                snapshot = (
                    await loaded
                    if inspect.isawaitable(loaded)
                    else loaded
                )
                if generation != self._generation:
                    continue
                self._snapshot = snapshot
                self._expires_at = time.monotonic() + self._ttl_seconds
                return snapshot

    def invalidate(self) -> None:
        self._generation += 1
        self._snapshot = None
        self._expires_at = 0.0


def _view_query(sql: str) -> str:
    statement = parse_one(sql, dialect="duckdb")
    if isinstance(statement, exp.Create) and statement.expression is not None:
        return statement.expression.sql(dialect="duckdb")
    return sql


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
