"""Bounded completion metadata for interactive catalogue clients."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import zip_longest

import duckdb

from repository.catalogue.client import Catalogue

MAX_METADATA_RELATIONS = 5_000
MAX_METADATA_COLUMNS = 100_000
MAX_METADATA_FUNCTIONS = 5_000


class CatalogueMetadataLimitError(RuntimeError):
    """Raised when interactive metadata exceeds its explicit response bound."""


@dataclass(frozen=True, slots=True)
class CatalogueColumnMetadata:
    name: str
    data_type: str
    nullable: bool


@dataclass(frozen=True, slots=True)
class CatalogueRelationMetadata:
    catalog_name: str
    schema_name: str
    name: str
    kind: str
    columns: tuple[CatalogueColumnMetadata, ...]


@dataclass(frozen=True, slots=True)
class CatalogueFunctionParameterMetadata:
    name: str
    data_type: str | None


@dataclass(frozen=True, slots=True)
class CatalogueFunctionMetadata:
    catalog_name: str
    schema_name: str
    name: str
    kind: str
    description: str | None
    return_type: str | None
    parameters: tuple[CatalogueFunctionParameterMetadata, ...]
    varargs: str | None
    result_columns: tuple[CatalogueColumnMetadata, ...]


@dataclass(frozen=True, slots=True)
class CatalogueMetadata:
    catalog_name: str
    default_schema: str
    relations: tuple[CatalogueRelationMetadata, ...]
    functions: tuple[CatalogueFunctionMetadata, ...]


def read_catalogue_metadata(catalogue: Catalogue) -> CatalogueMetadata:
    """Read the live logical catalogue used to complete SQL in the web client."""

    catalog_name = catalogue.config.alias
    relation_rows = catalogue.connection.execute(
        """
        SELECT table_catalog, table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_catalog = ?
          AND table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name
        LIMIT ?
        """,
        [catalog_name, MAX_METADATA_RELATIONS + 1],
    ).fetchall()
    _require_within_limit(relation_rows, MAX_METADATA_RELATIONS, "relations")

    column_rows = catalogue.connection.execute(
        """
        SELECT
            table_catalog,
            table_schema,
            table_name,
            column_name,
            data_type,
            is_nullable
        FROM information_schema.columns
        WHERE table_catalog = ?
          AND table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name, ordinal_position
        LIMIT ?
        """,
        [catalog_name, MAX_METADATA_COLUMNS + 1],
    ).fetchall()
    _require_within_limit(column_rows, MAX_METADATA_COLUMNS, "columns")

    function_rows = catalogue.connection.execute(
        """
        SELECT
            database_name,
            schema_name,
            function_name,
            function_type,
            coalesce(comment, description),
            return_type,
            parameters,
            parameter_types,
            varargs
        FROM duckdb_functions()
        WHERE (database_name = ? OR NOT internal)
          AND function_type IN ('scalar', 'aggregate', 'table', 'macro', 'table_macro')
        ORDER BY database_name, schema_name, function_name, function_type, parameters
        LIMIT ?
        """,
        [catalog_name, MAX_METADATA_FUNCTIONS + 1],
    ).fetchall()
    _require_within_limit(function_rows, MAX_METADATA_FUNCTIONS, "functions")

    columns_by_relation: defaultdict[
        tuple[str, str, str], list[CatalogueColumnMetadata]
    ] = defaultdict(list)
    for row in column_rows:
        columns_by_relation[(str(row[0]), str(row[1]), str(row[2]))].append(
            CatalogueColumnMetadata(
                name=str(row[3]),
                data_type=str(row[4]),
                nullable=str(row[5]).upper() == "YES",
            )
        )

    relations = tuple(
        CatalogueRelationMetadata(
            catalog_name=str(row[0]),
            schema_name=str(row[1]),
            name=str(row[2]),
            kind="view" if str(row[3]).upper() == "VIEW" else "table",
            columns=tuple(
                columns_by_relation[(str(row[0]), str(row[1]), str(row[2]))]
            ),
        )
        for row in relation_rows
    )
    namespace = ".".join(
        _quote_identifier(value)
        for value in (catalogue.config.alias, catalogue.config.schema)
    )
    catalogue.connection.execute(f"USE {namespace}")
    functions = tuple(
        _function_metadata(
            row,
            result_columns=_table_macro_result_columns(catalogue, row),
        )
        for row in function_rows
    )

    return CatalogueMetadata(
        catalog_name=catalog_name,
        default_schema=catalogue.config.schema,
        relations=relations,
        functions=functions,
    )


def _function_metadata(
    row: tuple[object, ...],
    *,
    result_columns: tuple[CatalogueColumnMetadata, ...] = (),
) -> CatalogueFunctionMetadata:
    names = tuple(str(value) for value in (row[6] or ()))
    types = tuple(str(value) if value is not None else None for value in (row[7] or ()))
    parameters = tuple(
        CatalogueFunctionParameterMetadata(name=name, data_type=data_type)
        for name, data_type in zip_longest(names, types, fillvalue=None)
        if name is not None
    )
    return CatalogueFunctionMetadata(
        catalog_name=str(row[0]),
        schema_name=str(row[1]),
        name=str(row[2]),
        kind=str(row[3]),
        description=str(row[4]) if row[4] is not None else None,
        return_type=str(row[5]) if row[5] is not None else None,
        parameters=parameters,
        varargs=str(row[8]) if row[8] is not None else None,
        result_columns=result_columns,
    )


def _table_macro_result_columns(
    catalogue: Catalogue, row: tuple[object, ...]
) -> tuple[CatalogueColumnMetadata, ...]:
    if str(row[0]) != catalogue.config.alias or str(row[3]) != "table_macro":
        return ()

    qualified_name = ".".join(
        _quote_identifier(str(value)) for value in (row[0], row[1], row[2])
    )
    arguments = ", ".join("NULL" for _ in (row[6] or ()))
    try:
        described = catalogue.connection.execute(
            f"DESCRIBE SELECT * FROM {qualified_name}({arguments})"
        ).fetchall()
    except duckdb.Error:
        # Some table macros derive their schema from runtime SQL or parameter values.
        return ()

    return tuple(
        CatalogueColumnMetadata(
            name=str(column[0]),
            data_type=str(column[1]),
            nullable=str(column[2]).upper() == "YES",
        )
        for column in described
    )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _require_within_limit(rows: list[tuple], limit: int, noun: str) -> None:
    if len(rows) > limit:
        raise CatalogueMetadataLimitError(
            f"catalogue metadata exceeds the interactive limit of {limit:,} {noun}"
        )
