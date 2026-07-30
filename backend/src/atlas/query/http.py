"""Bounded read-only SQL access to the public Atlas catalogue."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

import duckdb
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from atlas.platform.catalogue.control import CatalogueControl, get_catalogue_control
from atlas.platform.catalogue.public import (
    KNOWN_PUBLIC_OBJECTS,
    PUBLIC_OBJECTS,
    PUBLIC_SCHEMAS,
    WEB_SCHEMA,
    installed_public_objects,
)


router = APIRouter(prefix="/sql", tags=["sql"])
_MAX_ROWS = 10_000
_MAX_SQL_BYTES = 100_000
_READABLE_SCHEMAS = frozenset(PUBLIC_SCHEMAS)
_EXPLAIN_PREFIX = re.compile(r"^EXPLAIN\s+(?:ANALYZE\s+)?", re.IGNORECASE)
_FORBIDDEN_FUNCTIONS = frozenset(
    {
        "attach",
        "current_setting",
        "duckdb_secrets",
        "duckdb_settings",
        "getenv",
        "glob",
        "http_get",
        "http_post",
        "parquet_scan",
        "postgres_query",
        "postgres_scan",
        "query",
        "query_table",
        "read_blob",
        "read_csv",
        "read_csv_auto",
        "read_json",
        "read_json_auto",
        "read_ndjson",
        "read_parquet",
        "read_text",
        "which_secret",
    }
)
_FORBIDDEN_RELATION_PREFIXES = (
    "duckdb_",
    "pg_",
    "pragma_",
    "sqlite_",
)


class SqlQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=_MAX_SQL_BYTES)


class SqlQueryResponse(BaseModel):
    columns: tuple[str, ...]
    types: tuple[str, ...]
    rows: list[list[Any]]
    truncated: bool


class SqlColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool
    description: str | None


class SqlRelation(BaseModel):
    schema_name: Literal["web", "dom"]
    name: str
    kind: Literal["view"]
    description: str | None
    columns: list[SqlColumn]


class SqlMacroParameter(BaseModel):
    name: str
    data_type: str


class SqlMacro(BaseModel):
    schema_name: Literal["web", "dom"]
    name: str
    kind: Literal["scalar_macro", "table_macro"]
    parameters: list[SqlMacroParameter]
    return_type: str | None
    columns: list[SqlColumn]


class SqlMetadataResponse(BaseModel):
    catalogue_version: str
    relations: list[SqlRelation]
    macros: list[SqlMacro]


@router.post("/query", response_model=SqlQueryResponse)
async def query(
    payload: SqlQueryRequest,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> SqlQueryResponse:
    try:
        sql = _bounded_query(payload.sql)
        columns, types, rows = await control.run(
            lambda _session, catalogue: catalogue.trusted_remote_result(sql)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except duckdb.Error as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SqlQueryResponse(
        columns=columns,
        types=types,
        rows=[list(row) for row in rows[:_MAX_ROWS]],
        truncated=len(rows) > _MAX_ROWS,
    )


@router.get("/metadata", response_model=SqlMetadataResponse)
async def metadata(
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> SqlMetadataResponse:
    version, rows, macro_rows = await control.run(
        lambda _session, catalogue: _public_metadata(catalogue)
    )
    relations: dict[tuple[str, str], SqlRelation] = {}
    for (
        schema_name,
        table_name,
        relation_description,
        column_name,
        data_type,
        nullable,
        column_description,
    ) in rows:
        key = (str(schema_name), str(table_name))
        relation = relations.setdefault(
            key,
            SqlRelation(
                schema_name=str(schema_name),
                name=str(table_name),
                kind="view",
                description=(
                    str(relation_description)
                    if relation_description is not None
                    else None
                ),
                columns=[],
            ),
        )
        relation.columns.append(
            SqlColumn(
                name=str(column_name),
                data_type=str(data_type),
                nullable=bool(nullable),
                description=(
                    str(column_description)
                    if column_description is not None
                    else None
                ),
            )
        )
    macros = [
        SqlMacro(
            schema_name=item.schema,
            name=item.name,
            kind="table_macro" if item.kind == "table_macro" else "scalar_macro",
            parameters=[
                SqlMacroParameter(name=name, data_type=data_type)
                for name, data_type in item.parameters
            ],
            return_type=item.return_type,
            columns=[
                SqlColumn(
                    name=str(column[0]),
                    data_type=str(column[1]),
                    nullable=str(column[2]).upper() == "YES",
                    description=None,
                )
                for column in macro_rows.get((item.schema, item.name), ())
            ],
        )
        for item in KNOWN_PUBLIC_OBJECTS
        if item.kind in {"macro", "table_macro"}
        and item.exposed
        and (
            item.kind == "macro"
            or (item.schema, item.name) in macro_rows
        )
    ]
    return SqlMetadataResponse(
        catalogue_version=version,
        relations=list(relations.values()),
        macros=macros,
    )


def _public_metadata(
    catalogue,
) -> tuple[str, list[tuple], dict[tuple[str, str], list[tuple]]]:
    version_rows = catalogue.trusted_remote_rows(
        f"SELECT {WEB_SCHEMA}._catalogue_version()"
    )
    if len(version_rows) != 1:
        raise RuntimeError("public catalogue version query returned no value")
    macro_rows: dict[tuple[str, str], list[tuple]] = {}
    for item in installed_public_objects(catalogue):
        if item.kind != "table_macro" or not item.exposed:
            continue
        if item.arguments_sql is None:
            raise RuntimeError(
                f"{item.schema}.{item.name} has no metadata arguments"
            )
        macro_rows[(item.schema, item.name)] = catalogue.trusted_remote_rows(
            f"DESCRIBE SELECT * FROM {item.schema}.{item.name}"
            f"({item.arguments_sql})"
        )
    return str(version_rows[0][0]), _public_metadata_rows(catalogue), macro_rows


def _public_metadata_rows(catalogue) -> list[tuple]:
    rows = catalogue.trusted_remote_rows(
        """
        SELECT views.schema_name,
               views.view_name,
               views.comment,
               columns.column_name,
               columns.data_type,
               columns.is_nullable
        FROM duckdb_views() AS views
        JOIN duckdb_columns() AS columns
          ON columns.database_oid = views.database_oid
         AND columns.schema_oid = views.schema_oid
         AND columns.table_oid = views.view_oid
        WHERE views.database_name = current_catalog()
          AND views.schema_name IN ('web', 'dom')
        ORDER BY views.schema_name,
                 views.view_name,
                 columns.column_index
        """
    )
    descriptions = {
        (item.schema, item.name): dict(item.column_comments)
        for item in PUBLIC_OBJECTS
        if item.kind == "view"
    }
    return [
        (
            schema_name,
            view_name,
            view_comment,
            column_name,
            data_type,
            nullable,
            descriptions[(str(schema_name), str(view_name))][
                str(column_name)
            ],
        )
        for (
            schema_name,
            view_name,
            view_comment,
            column_name,
            data_type,
            nullable,
        ) in rows
    ]


def _bounded_query(sql: str, *, max_rows: int = _MAX_ROWS) -> str:
    source = sql.strip()
    normalized = source.removesuffix(";").rstrip()
    explain_prefix = _EXPLAIN_PREFIX.match(normalized)
    if explain_prefix is not None:
        explained = normalized[explain_prefix.end() :].strip()
        statement = _one_statement(explained)
        if not isinstance(statement, exp.Query):
            raise ValueError("EXPLAIN accepts one read-only query")
        _validate_catalogue_access(statement)
        return normalized

    statement = _one_statement(source)
    if isinstance(statement, exp.Query):
        _validate_catalogue_access(statement)
        return (
            f"SELECT * FROM ({normalized}) AS atlas_console_query "
            f"LIMIT {max_rows + 1}"
        )
    if isinstance(statement, (exp.Describe, exp.Summarize)):
        target = statement.this
        if not isinstance(target, (exp.Query, exp.Table)):
            raise ValueError(
                f"{statement.key.upper()} requires a public relation "
                "or read-only query"
            )
        _validate_catalogue_access(statement)
        return normalized
    if isinstance(statement, exp.Show):
        source_schema = statement.args.get("from_")
        if (
            str(statement.this).upper() == "TABLES"
            and isinstance(source_schema, exp.Table)
            and not source_schema.db
            and source_schema.name.lower() in _READABLE_SCHEMAS
        ):
            return normalized
        raise ValueError(
            "SHOW is limited to SHOW TABLES FROM web or SHOW TABLES FROM dom"
        )
    raise ValueError(
        "SQL console accepts one read-only query or public inspection statement"
    )


def _one_statement(sql: str) -> exp.Expression:
    try:
        statements = parse(sql, read="duckdb")
    except ParseError as exc:
        raise ValueError(str(exc)) from exc
    if len(statements) != 1 or statements[0] is None:
        raise ValueError("SQL console accepts exactly one statement")
    return statements[0]


def _validate_catalogue_access(statement: exp.Expression) -> None:
    ctes = {
        cte.alias_or_name.lower()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }
    for table in statement.find_all(exp.Table):
        if table.catalog:
            raise ValueError("SQL console does not accept explicit catalog names")
        if table.db and table.db.lower() not in _READABLE_SCHEMAS:
            raise ValueError("SQL console may only read web.* or dom.*")
        name = table.name.lower()
        if name in ctes:
            continue
        if name.startswith(_FORBIDDEN_RELATION_PREFIXES):
            raise ValueError("SQL console may not read system relations")
        if not table.db:
            raise ValueError(
                "catalogue relations must be qualified with web or dom"
            )
    for function in statement.find_all(exp.Func):
        if function.name.lower() in _FORBIDDEN_FUNCTIONS:
            raise ValueError(f"SQL console may not call {function.name}")
