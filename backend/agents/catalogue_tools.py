"""Typed, read-only catalogue capabilities shared by Atlas agents."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from atlas_sql import (
    AtlasCompiler,
    CompilationResult,
    InteractiveQueryPurpose,
)
from repository.catalogue.interactive import execute_interactive_query
from repository.catalogue.quack_runtime import QuackQueryRuntime, trusted_remote_rows
from repository.catalogue.query import validate_interactive_catalogue_statement


class CatalogueColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    data_type: str
    nullable: bool
    description: str | None = None


class CatalogueRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qualified_name: str
    kind: Literal["table", "view"]
    description: str | None = None
    columns: list[CatalogueColumn] = Field(default_factory=list)


class CatalogueMacro(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qualified_name: str
    kind: str
    description: str | None
    parameters: list[str]
    return_type: str | None


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
    compilation: CompilationResult


class CatalogueSqlSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    authored_sql: str = Field(min_length=1, max_length=100_000)
    executable_sql: str = Field(min_length=1, max_length=100_000)
    compiler_fingerprint: str | None = None
    catalogue_revision: str | None = None


class CatalogueTools:
    """Application-facing read capabilities, independent of agent or MCP transport."""

    def __init__(
        self,
        query_runtime: QuackQueryRuntime,
        *,
        compiler: AtlasCompiler | None = None,
        compiler_purpose: InteractiveQueryPurpose | None = None,
        macro_descriptions: dict[str, str] | None = None,
    ) -> None:
        self.query_runtime = query_runtime
        self.compiler = compiler
        self.compiler_purpose = compiler_purpose
        self.macro_descriptions = macro_descriptions or {}

    async def list_relations(
        self, kind: Literal["table", "view"]
    ) -> list[CatalogueRelation]:
        config = self.query_runtime.config

        def operation(connection):
            rows = trusted_remote_rows(
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
            rows = trusted_remote_rows(
                connection,
                f"""
                SELECT t.table_schema, t.table_name, t.table_type,
                       coalesce(dt.comment, dv.comment) AS relation_comment,
                       c.column_name, c.data_type, c.is_nullable,
                       dc.comment AS column_comment
                FROM information_schema.tables AS t
                JOIN information_schema.columns AS c USING (
                  table_catalog, table_schema, table_name
                )
                LEFT JOIN duckdb_tables() AS dt
                  ON dt.database_name = t.table_catalog
                 AND dt.schema_name = t.table_schema
                 AND dt.table_name = t.table_name
                LEFT JOIN duckdb_views() AS dv
                  ON dv.database_name = t.table_catalog
                 AND dv.schema_name = t.table_schema
                 AND dv.view_name = t.table_name
                LEFT JOIN duckdb_columns() AS dc
                  ON dc.database_name = c.table_catalog
                 AND dc.schema_name = c.table_schema
                 AND dc.table_name = c.table_name
                 AND dc.column_name = c.column_name
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
                description=(
                    None if rows[0][3] is None else str(rows[0][3])
                ),
                columns=[
                    CatalogueColumn(
                        name=str(row[4]),
                        data_type=str(row[5]),
                        nullable=str(row[6]).upper() == "YES",
                        description=(
                            None if row[7] is None else str(row[7])
                        ),
                    )
                    for row in rows
                ],
            )

        return await self.query_runtime.run_internal(operation)

    async def list_macros(self) -> list[CatalogueMacro]:
        config = self.query_runtime.config

        def operation(connection):
            rows = trusted_remote_rows(
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
                    description=(
                        self.macro_descriptions.get(f"{row[0]}.{row[1]}")
                        or (None if row[3] is None else str(row[3]))
                    ),
                    parameters=_string_list(row[4]),
                    return_type=None if row[5] is None else str(row[5]),
                )
                for row in rows
            ]

        return await self.query_runtime.run_internal(operation)

    async def query(self, sql: str) -> CatalogueQueryResult:
        statement = validate_interactive_catalogue_statement(
            sql,
            catalogue_alias=self.query_runtime.config.catalogue_alias,
            catalogue_schema=self.query_runtime.config.catalogue_schema,
        )
        bounded_sql = statement.query.limit(201).sql(dialect="duckdb")
        compilation = self.compile(bounded_sql)
        result = await execute_interactive_query(
            self.query_runtime,
            bounded_sql,
            compilation=compilation,
        )
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
            compilation=compilation,
        )

    def compile(self, sql: str) -> CompilationResult:
        """Compile and lint SQL without executing it."""

        return (self.compiler or AtlasCompiler.embedded()).compile(
            sql,
            purpose=self.compiler_purpose or InteractiveQueryPurpose(),
            coverage_source="agent_lint",
        )

    def prepare_suggestion(
        self,
        *,
        title: str,
        description: str,
        sql: str,
    ) -> CatalogueSqlSuggestion:
        """Validate and compile a clean, read-only SQL handoff."""

        validate_interactive_catalogue_statement(
            sql,
            catalogue_alias=self.query_runtime.config.catalogue_alias,
            catalogue_schema=self.query_runtime.config.catalogue_schema,
        )
        compilation = self.compile(sql)
        if not compilation.valid or compilation.diagnostics:
            diagnostics = "; ".join(
                f"{diagnostic.severity}: {diagnostic.code}: {diagnostic.message}"
                for diagnostic in compilation.diagnostics
            )
            raise ValueError(
                "Suggested SQL must compile without errors or warnings"
                + (f": {diagnostics}" if diagnostics else ".")
            )
        executable_sql = compilation.executable_sql or compilation.authored_sql
        return CatalogueSqlSuggestion(
            title=title,
            description=description,
            authored_sql=compilation.authored_sql,
            executable_sql=executable_sql,
            compiler_fingerprint=compilation.fingerprint,
            catalogue_revision=compilation.catalogue_revision,
        )


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
