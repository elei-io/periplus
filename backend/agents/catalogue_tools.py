"""Typed, read-only catalogue capabilities shared by Atlas agents."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from api.catalogue_control import CatalogueControl
from control.catalogue_materializations.service import list_records
from repository.catalogue.interactive import execute_interactive_query
from repository.catalogue.quack_runtime import QuackQueryRuntime, remote_rows
from repository.catalogue.query import validate_interactive_catalogue_statement


class CatalogueColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    data_type: str
    nullable: bool


class CatalogueRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qualified_name: str
    kind: Literal["table", "view"]
    columns: list[CatalogueColumn] = Field(default_factory=list)


class CatalogueMacro(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qualified_name: str
    kind: str
    description: str | None
    parameters: list[str]
    return_type: str | None


class CatalogueMaterialization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qualified_name: str
    display_name: str
    description: str | None
    source_table: str
    refresh_strategy: str
    key_columns: list[str]
    status: str
    last_refreshed_at: str | None


class CatalogueQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "unavailable"] = "completed"
    query_id: str | None
    sql: str
    columns: list[str]
    column_types: list[str]
    rows: list[list[Any]]
    row_count: int = 0
    truncated: bool = False
    error: str | None = None


class CatalogueTools:
    """Application-facing read capabilities, independent of agent or MCP transport."""

    def __init__(
        self,
        query_runtime: QuackQueryRuntime,
        catalogue_control: CatalogueControl,
    ) -> None:
        self.query_runtime = query_runtime
        self.catalogue_control = catalogue_control

    async def list_relations(
        self, kind: Literal["table", "view"]
    ) -> list[CatalogueRelation]:
        config = self.query_runtime.config

        def operation(connection):
            rows = remote_rows(
                connection,
                f"""
                SELECT table_schema, table_name, table_type
                FROM information_schema.tables
                WHERE table_catalog = {_quote_literal(config.catalogue_alias)}
                  AND table_schema IN (
                    {_quote_literal(config.catalogue_schema)}, 'views'
                  )
                  AND (table_type = 'VIEW') = {str(kind == 'view').lower()}
                ORDER BY table_schema, table_name
                LIMIT 501
                """,
            )
            if len(rows) > 500:
                raise ValueError("Catalogue relation listing exceeds 500 entries.")
            return [
                CatalogueRelation(
                    qualified_name=f"{row[0]}.{row[1]}",
                    kind="view" if str(row[2]).upper() == "VIEW" else "table",
                )
                for row in rows
            ]

        return await self.query_runtime.run_internal(operation)

    async def describe_relation(self, qualified_name: str) -> CatalogueRelation:
        normalized = qualified_name.strip().lower()
        config = self.query_runtime.config

        def operation(connection):
            rows = remote_rows(
                connection,
                f"""
                SELECT t.table_schema, t.table_name, t.table_type,
                       c.column_name, c.data_type, c.is_nullable
                FROM information_schema.tables AS t
                JOIN information_schema.columns AS c USING (
                  table_catalog, table_schema, table_name
                )
                WHERE t.table_catalog = {_quote_literal(config.catalogue_alias)}
                  AND t.table_schema IN (
                    {_quote_literal(config.catalogue_schema)}, 'views'
                  )
                  AND lower(t.table_schema || '.' || t.table_name) =
                      {_quote_literal(normalized)}
                ORDER BY c.ordinal_position
                LIMIT 1001
                """,
            )
            if not rows:
                raise ValueError(f"Catalogue relation {qualified_name!r} was not found.")
            if len(rows) > 1_000:
                raise ValueError("Catalogue relation exceeds 1,000 columns.")
            return CatalogueRelation(
                qualified_name=f"{rows[0][0]}.{rows[0][1]}",
                kind=(
                    "view" if str(rows[0][2]).upper() == "VIEW" else "table"
                ),
                columns=[
                    CatalogueColumn(
                        name=str(row[3]),
                        data_type=str(row[4]),
                        nullable=str(row[5]).upper() == "YES",
                    )
                    for row in rows
                ],
            )

        return await self.query_runtime.run_internal(operation)

    async def list_macros(self) -> list[CatalogueMacro]:
        config = self.query_runtime.config

        def operation(connection):
            rows = remote_rows(
                connection,
                f"""
                SELECT schema_name, function_name, function_type,
                       coalesce(comment, description), parameters, return_type
                FROM duckdb_functions()
                WHERE database_name = {_quote_literal(config.catalogue_alias)}
                  AND function_type IN ('macro', 'table_macro')
                ORDER BY schema_name, function_name
                LIMIT 501
                """,
            )
            if len(rows) > 500:
                raise ValueError("Catalogue macro listing exceeds 500 entries.")
            return [
                CatalogueMacro(
                    qualified_name=f"{row[0]}.{row[1]}",
                    kind=str(row[2]),
                    description=None if row[3] is None else str(row[3]),
                    parameters=_string_list(row[4]),
                    return_type=None if row[5] is None else str(row[5]),
                )
                for row in rows
            ]

        return await self.query_runtime.run_internal(operation)

    async def list_materializations(self) -> list[CatalogueMaterialization]:
        records = await self.catalogue_control.run(
            lambda session, _catalogue: list_records(session)
        )
        return [
            CatalogueMaterialization(
                qualified_name=record.qualified_name,
                display_name=record.display_name,
                description=record.description,
                source_table=record.source_table,
                refresh_strategy=record.refresh_strategy,
                key_columns=record.key_columns,
                status=record.observed_state,
                last_refreshed_at=(
                    record.last_refreshed_at.isoformat()
                    if record.last_refreshed_at is not None
                    else None
                ),
            )
            for record in records
        ]

    async def query(self, sql: str) -> CatalogueQueryResult:
        statement = validate_interactive_catalogue_statement(
            sql,
            catalogue_alias=self.query_runtime.config.catalogue_alias,
            catalogue_schema=self.query_runtime.config.catalogue_schema,
        )
        bounded_sql = statement.query.limit(201).sql(dialect="duckdb")
        result = await execute_interactive_query(self.query_runtime, bounded_sql)
        truncated = len(result.rows) > 200
        rows = result.rows[:200]
        return CatalogueQueryResult(
            query_id=result.query_id.hex,
            sql=bounded_sql,
            columns=result.columns,
            column_types=result.column_types,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
        )


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
