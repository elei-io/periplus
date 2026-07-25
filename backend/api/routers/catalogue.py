"""Server-owned interactive catalogue query API."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from atlas_sql import (
    AnalysisResult,
    AtlasCompiler,
    COMPILER_VERSION,
    CompilationResult,
    InteractiveQueryPurpose,
)
from repository.catalogue.compiler_definitions import (
    CatalogueCompilerDefinitionCache,
    CatalogueCompilerDefinitions,
)
from repository.catalogue.quack_runtime import (
    CatalogueQueryExecutionError,
    QuackQueryRuntime,
    trusted_remote_rows,
)
from repository.catalogue.interactive import prepare_interactive_query
from repository.catalogue.analysis import analyze_compilation
from repository.catalogue.query import (
    CatalogueQueryError,
    CatalogueStatementKind,
)
from runtime.catalogue_queries import (
    CatalogueQueryState,
    get_catalogue_query,
    request_catalogue_query_cancellation,
    update_catalogue_query,
)


router = APIRouter(prefix="/catalogue", tags=["catalogue"])
ARROW_STREAM_MEDIA_TYPE = "application/vnd.apache.arrow.stream"


class CatalogueSqlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=100_000)


class CatalogueCompileRequest(CatalogueSqlRequest):
    purpose: Literal["interactive"] = "interactive"


class CatalogueAnalyzeRequest(CatalogueCompileRequest):
    per_plan_budget_seconds: float = Field(default=60.0, gt=0, le=600)
    execution_policy: Literal["cold", "warm"] = "cold"
    warmup_runs: int = Field(default=1, ge=0, le=10)
    equivalence_hashes: bool = False


class CatalogueQueryRuntimeResponse(BaseModel):
    transport: Literal["api"] = "api"
    mutation_policy: Literal["read_only"] = "read_only"
    result_format: Literal["arrow_ipc_stream"] = "arrow_ipc_stream"
    cancellation_supported: bool = True
    maximum_concurrency: int
    query_timeout_seconds: float
    maximum_rows: int
    maximum_result_bytes: int


class CatalogueStatusResponse(BaseModel):
    lake_slug: str
    active_file_count: int
    active_storage_bytes: int
    ducklake_version: str | None
    catalogue_schema_version: str
    compiler_version: str


class CatalogueMetadataColumnResponse(BaseModel):
    name: str
    data_type: str
    nullable: bool


class CatalogueMetadataRelationResponse(BaseModel):
    catalog_name: str
    schema_name: str
    name: str
    kind: Literal["table", "view"]
    columns: list[CatalogueMetadataColumnResponse]


class CatalogueMetadataFunctionParameterResponse(BaseModel):
    name: str
    data_type: str | None


class CatalogueMetadataFunctionResponse(BaseModel):
    catalog_name: str
    schema_name: str
    name: str
    kind: str
    description: str | None
    return_type: str | None
    parameters: list[CatalogueMetadataFunctionParameterResponse]
    varargs: str | None
    result_columns: list[CatalogueMetadataColumnResponse]


class CatalogueMetadataResponse(BaseModel):
    catalog_name: str
    default_schema: str
    relations: list[CatalogueMetadataRelationResponse]
    functions: list[CatalogueMetadataFunctionResponse]


def get_quack_runtime(request: Request) -> QuackQueryRuntime:
    runtime = getattr(request.app.state, "quack_runtime", None)
    if not isinstance(runtime, QuackQueryRuntime):
        raise RuntimeError("API Quack query runtime is unavailable.")
    return runtime


def get_compiler_definition_cache(
    request: Request,
) -> CatalogueCompilerDefinitionCache:
    cache = getattr(request.app.state, "compiler_definitions", None)
    if not isinstance(cache, CatalogueCompilerDefinitionCache):
        raise RuntimeError("Catalogue compiler definitions are unavailable.")
    return cache


async def get_compiler_definitions(
    cache: Annotated[
        CatalogueCompilerDefinitionCache,
        Depends(get_compiler_definition_cache),
    ],
) -> CatalogueCompilerDefinitions:
    return await cache.get()


@router.get("/query-runtime", response_model=CatalogueQueryRuntimeResponse)
def query_runtime(request: Request) -> CatalogueQueryRuntimeResponse:
    config = get_quack_runtime(request).config
    return CatalogueQueryRuntimeResponse(
        maximum_concurrency=config.maximum_concurrency,
        query_timeout_seconds=config.query_timeout_seconds,
        maximum_rows=config.maximum_rows,
        maximum_result_bytes=config.maximum_result_bytes,
    )


@router.post("/query-executions")
async def execute_query(
    payload: CatalogueSqlRequest,
    request: Request,
    definitions: Annotated[
        CatalogueCompilerDefinitions,
        Depends(get_compiler_definitions),
    ],
) -> StreamingResponse:
    try:
        query_id, statement, active = await prepare_interactive_query(
            get_quack_runtime(request),
            payload.sql,
            compiler=AtlasCompiler.embedded(
                catalogue_revision=definitions.revision
            ),
            purpose=definitions.interactive_purpose(),
        )
    except CatalogueQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CatalogueQueryExecutionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    async def stream():
        async for chunk in active.stream():
            yield chunk

    return StreamingResponse(
        stream(),
        media_type=ARROW_STREAM_MEDIA_TYPE,
        headers={
            "X-Atlas-Query-ID": query_id.hex,
            "X-Atlas-Statement-Kind": statement.kind.value,
            "Cache-Control": "no-store",
        },
    )


@router.get("/query-executions/{query_id}", response_model=CatalogueQueryState)
async def query_status(query_id: UUID, request: Request) -> CatalogueQueryState:
    state = await get_catalogue_query(
        get_quack_runtime(request).query_bucket,
        query_id,
    )
    if state is None:
        raise HTTPException(status_code=404, detail="Catalogue query not found.")
    return state


@router.delete("/query-executions/{query_id}", response_model=CatalogueQueryState)
async def cancel_query(query_id: UUID, request: Request) -> CatalogueQueryState:
    runtime = get_quack_runtime(request)
    state = await update_catalogue_query(
        runtime.query_bucket,
        query_id,
        request_catalogue_query_cancellation,
    )
    if state is None:
        raise HTTPException(status_code=404, detail="Catalogue query not found.")
    if state.status not in {"succeeded", "failed", "cancelled"}:
        runtime.interrupt_local(query_id, "query cancellation was requested")
    return state


@router.get("/status", response_model=CatalogueStatusResponse)
async def catalogue_status(request: Request) -> CatalogueStatusResponse:
    runtime = get_quack_runtime(request)
    config = runtime.config

    def operation(connection):
        rows = trusted_remote_rows(
            connection,
            f"""
            SELECT coalesce(sum(file_count), 0),
                   coalesce(sum(file_size_bytes), 0)
            FROM ducklake_table_info({_quote_literal(config.catalogue_alias)})
            """,
        )
        versions = trusted_remote_rows(
            connection,
            """
            SELECT extension_version
            FROM duckdb_extensions()
            WHERE extension_name = 'ducklake' AND installed
            """,
        )
        return CatalogueStatusResponse(
            lake_slug=config.lake_slug,
            active_file_count=int(rows[0][0] if rows else 0),
            active_storage_bytes=int(rows[0][1] if rows else 0),
            ducklake_version=(
                str(versions[0][0])
                if versions and versions[0][0] is not None
                else None
            ),
            catalogue_schema_version=config.catalogue_schema_version,
            compiler_version=COMPILER_VERSION,
        )

    try:
        return await runtime.run_internal(operation)
    except CatalogueQueryExecutionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/metadata", response_model=CatalogueMetadataResponse)
async def catalogue_metadata(request: Request) -> CatalogueMetadataResponse:
    runtime = get_quack_runtime(request)
    config = runtime.config

    def operation(connection):
        alias = _quote_literal(config.catalogue_alias)
        relation_rows = trusted_remote_rows(
            connection,
            f"""
            SELECT table_catalog, table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE table_catalog = {alias}
              AND table_schema NOT IN ('information_schema', 'pg_catalog')
            ORDER BY table_schema, table_name
            LIMIT 5001
            """,
        )
        if len(relation_rows) > 5_000:
            raise CatalogueQueryExecutionError(
                "Catalogue metadata exceeds the limit of 5,000 relations."
            )
        column_rows = trusted_remote_rows(
            connection,
            f"""
            SELECT table_catalog, table_schema, table_name, column_name,
                   data_type, is_nullable
            FROM information_schema.columns
            WHERE table_catalog = {alias}
              AND table_schema NOT IN ('information_schema', 'pg_catalog')
            ORDER BY table_schema, table_name, ordinal_position
            LIMIT 100001
            """,
        )
        if len(column_rows) > 100_000:
            raise CatalogueQueryExecutionError(
                "Catalogue metadata exceeds the limit of 100,000 columns."
            )
        function_rows = trusted_remote_rows(
            connection,
            """
            SELECT database_name, schema_name, function_name, function_type,
                   coalesce(comment, description), return_type, parameters,
                   parameter_types, varargs
            FROM duckdb_functions()
            WHERE function_type IN (
              'scalar', 'aggregate', 'table', 'macro', 'table_macro'
            )
            ORDER BY database_name, schema_name, function_name,
                     function_type, parameters
            LIMIT 5001
            """,
        )
        if len(function_rows) > 5_000:
            raise CatalogueQueryExecutionError(
                "Catalogue metadata exceeds the limit of 5,000 functions."
            )

        columns_by_relation: dict[
            tuple[str, str, str],
            list[CatalogueMetadataColumnResponse],
        ] = {}
        for row in column_rows:
            key = (str(row[0]), str(row[1]), str(row[2]))
            columns_by_relation.setdefault(key, []).append(
                CatalogueMetadataColumnResponse(
                    name=str(row[3]),
                    data_type=str(row[4]),
                    nullable=str(row[5]).upper() == "YES",
                )
            )

        functions: list[CatalogueMetadataFunctionResponse] = []
        for row in function_rows:
            names = _array_values(row[6])
            types = _array_values(row[7])
            functions.append(
                CatalogueMetadataFunctionResponse(
                    catalog_name=str(row[0]),
                    schema_name=str(row[1]),
                    name=str(row[2]),
                    kind=str(row[3]),
                    description=None if row[4] is None else str(row[4]),
                    return_type=None if row[5] is None else str(row[5]),
                    parameters=[
                        CatalogueMetadataFunctionParameterResponse(
                            name=name,
                            data_type=types[index] if index < len(types) else None,
                        )
                        for index, name in enumerate(names)
                    ],
                    varargs=None if row[8] is None else str(row[8]),
                    result_columns=_table_macro_result_columns(
                        connection,
                        config.catalogue_alias,
                        row,
                    ),
                )
            )
        return CatalogueMetadataResponse(
            catalog_name=config.catalogue_alias,
            default_schema=config.catalogue_schema,
            relations=[
                CatalogueMetadataRelationResponse(
                    catalog_name=str(row[0]),
                    schema_name=str(row[1]),
                    name=str(row[2]),
                    kind=(
                        "view" if str(row[3]).upper() == "VIEW" else "table"
                    ),
                    columns=columns_by_relation.get(
                        (str(row[0]), str(row[1]), str(row[2])),
                        [],
                    ),
                )
                for row in relation_rows
            ],
            functions=functions,
        )

    try:
        return await runtime.run_internal(operation)
    except CatalogueQueryExecutionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/sql/compile", response_model=CompilationResult)
async def compile_sql(
    payload: CatalogueCompileRequest,
    definitions: Annotated[
        CatalogueCompilerDefinitions,
        Depends(get_compiler_definitions),
    ],
) -> CompilationResult:
    return compile_sql_result(
        payload,
        purpose=definitions.interactive_purpose(),
        catalogue_revision=definitions.revision,
    )


def compile_sql_result(
    payload: CatalogueSqlRequest,
    *,
    purpose: InteractiveQueryPurpose | None = None,
    catalogue_revision: str | None = None,
) -> CompilationResult:
    return AtlasCompiler.embedded(
        catalogue_revision=catalogue_revision,
    ).compile(
        payload.sql,
        purpose=purpose or InteractiveQueryPurpose(),
    )


@router.post("/sql/analyze", response_model=AnalysisResult)
async def analyze_sql(
    payload: CatalogueAnalyzeRequest,
    request: Request,
    definitions: Annotated[
        CatalogueCompilerDefinitions,
        Depends(get_compiler_definitions),
    ],
) -> AnalysisResult:
    compilation = compile_sql_result(
        payload,
        purpose=definitions.interactive_purpose(),
        catalogue_revision=definitions.revision,
    )
    try:
        return await analyze_compilation(
            get_quack_runtime(request),
            compilation,
            per_plan_budget_seconds=payload.per_plan_budget_seconds,
            execution_policy=payload.execution_policy,
            warmup_runs=payload.warmup_runs,
            equivalence_hashes=payload.equivalence_hashes,
        )
    except CatalogueQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CatalogueQueryExecutionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _table_macro_result_columns(
    connection,
    catalogue_alias: str,
    row: tuple,
) -> list[CatalogueMetadataColumnResponse]:
    if str(row[0]) != catalogue_alias or str(row[3]) != "table_macro":
        return []
    qualified = ".".join(
        _quote_identifier(str(value)) for value in row[:3]
    )
    arguments = ", ".join("NULL" for _ in _array_values(row[6]))
    try:
        rows = trusted_remote_rows(
            connection,
            f"DESCRIBE SELECT * FROM {qualified}({arguments})",
        )
    except Exception:
        return []
    return [
        CatalogueMetadataColumnResponse(
            name=str(column[0]),
            data_type=str(column[1]),
            nullable=str(column[2]).upper() == "YES",
        )
        for column in rows
    ]


def _array_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
