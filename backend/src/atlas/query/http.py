"""Bounded read-only SQL access to the public Atlas web catalogue."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

import duckdb
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from atlas.platform.catalogue.control import CatalogueControl, get_catalogue_control


router = APIRouter(prefix="/sql", tags=["sql"])
_MAX_ROWS = 10_000
_MAX_SQL_BYTES = 100_000
_READABLE_SCHEMAS = frozenset({"web"})
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
        "quack_query",
        "quack_query_by_name",
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
    "quack_",
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


class SqlRelation(BaseModel):
    schema_name: Literal["web"]
    name: str
    kind: Literal["table", "view"]
    columns: list[SqlColumn]


class SqlMetadataResponse(BaseModel):
    relations: list[SqlRelation]


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
    rows = await control.run(
        lambda _session, catalogue: catalogue.trusted_remote_rows(
            """
            SELECT tables.table_schema,
                   tables.table_name,
                   tables.table_type,
                   columns.column_name,
                   columns.data_type,
                   columns.is_nullable
            FROM information_schema.tables AS tables
            JOIN information_schema.columns AS columns
              USING (table_catalog, table_schema, table_name)
            WHERE tables.table_catalog = current_catalog()
              AND tables.table_schema = 'web'
            ORDER BY tables.table_schema, tables.table_name,
                     columns.ordinal_position
            """
        )
    )
    relations: dict[tuple[str, str], SqlRelation] = {}
    for schema_name, table_name, table_type, column_name, data_type, nullable in rows:
        key = (str(schema_name), str(table_name))
        relation = relations.setdefault(
            key,
            SqlRelation(
                schema_name=str(schema_name),
                name=str(table_name),
                kind="view" if str(table_type).upper() == "VIEW" else "table",
                columns=[],
            ),
        )
        relation.columns.append(
            SqlColumn(
                name=str(column_name),
                data_type=str(data_type),
                nullable=str(nullable).upper() == "YES",
            )
        )
    return SqlMetadataResponse(relations=list(relations.values()))


def _bounded_query(sql: str) -> str:
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
            f"LIMIT {_MAX_ROWS + 1}"
        )
    if isinstance(statement, (exp.Describe, exp.Summarize)):
        target = statement.this
        if not isinstance(target, (exp.Query, exp.Table)):
            raise ValueError(
                f"{statement.key.upper()} requires a web.* relation or read-only query"
            )
        _validate_catalogue_access(statement)
        return normalized
    if isinstance(statement, exp.Show):
        source_schema = statement.args.get("from_")
        if (
            str(statement.this).upper() == "TABLES"
            and isinstance(source_schema, exp.Table)
            and not source_schema.db
            and source_schema.name.lower() == "web"
        ):
            return normalized
        raise ValueError("SHOW is limited to SHOW TABLES FROM web")
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
            raise ValueError("SQL console may only read web.*")
        name = table.name.lower()
        if name in ctes:
            continue
        if name.startswith(_FORBIDDEN_RELATION_PREFIXES):
            raise ValueError("SQL console may not read system relations")
        if not table.db:
            raise ValueError("catalogue relations must be qualified with web")
    for function in statement.find_all(exp.Func):
        if function.name.lower() in _FORBIDDEN_FUNCTIONS:
            raise ValueError(f"SQL console may not call {function.name}")
