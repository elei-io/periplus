"""Shared execution boundary for validated interactive catalogue reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

import pyarrow as pa
from pydantic_core import to_jsonable_python

from atlas_sql import (
    AtlasCompiler,
    CompilationResult,
    InteractiveQueryPurpose,
    CompilationOutcome,
)
from repository.catalogue.quack_runtime import (
    ActiveCatalogueQuery,
    CatalogueQueryExecutionError,
    QuackQueryRuntime,
)
from repository.catalogue.query import (
    ClassifiedCatalogueStatement,
    validate_interactive_catalogue_statement,
)
from runtime.catalogue_queries import (
    CatalogueQueryAppliedRewrite,
    CatalogueQueryOptimizationDiagnostic,
    CatalogueQueryOptimizationStatus,
    CatalogueQueryState,
    create_catalogue_query,
    get_catalogue_query,
)


@dataclass(frozen=True, slots=True)
class BufferedCatalogueResult:
    query_id: UUID
    columns: list[str]
    column_types: list[str]
    rows: list[list[Any]]


async def prepare_interactive_query(
    runtime: QuackQueryRuntime,
    sql: str,
    *,
    compiler: AtlasCompiler | None = None,
    compilation: CompilationResult | None = None,
    purpose: InteractiveQueryPurpose | None = None,
) -> tuple[UUID, ClassifiedCatalogueStatement, ActiveCatalogueQuery]:
    """Validate, register, and prepare one query through the public read boundary."""

    statement = validate_interactive_catalogue_statement(
        sql,
        catalogue_alias=runtime.config.catalogue_alias,
        catalogue_schema=runtime.config.catalogue_schema,
    )
    await runtime.preflight(statement)
    compilation = compilation or (compiler or AtlasCompiler.embedded()).compile(
        statement.sql,
        purpose=purpose or InteractiveQueryPurpose(),
        coverage_source="interactive_execution",
    )
    if compilation.authored_sql != statement.sql:
        raise CatalogueQueryExecutionError(
            "The prepared compilation does not match the validated SQL."
        )
    if not compilation.valid or compilation.executable_sql is None:
        raise CatalogueQueryExecutionError(
            compilation.diagnostics[-1].message
        )
    optimization_status: CatalogueQueryOptimizationStatus = {
        CompilationOutcome.OPTIMIZED: "optimized",
        CompilationOutcome.UNCHANGED: "unchanged",
        CompilationOutcome.UNSUPPORTED: "degraded_fallback",
    }[compilation.outcome]
    applied_rewrites = tuple(
        CatalogueQueryAppliedRewrite(
            rule=rewrite.rule,
            evidence=rewrite.evidence,
        )
        for rewrite in compilation.applied_rewrites
    )
    optimization_diagnostics = tuple(
        CatalogueQueryOptimizationDiagnostic(
            code=diagnostic.code,
            severity=diagnostic.severity,
            message=diagnostic.message,
            documentation_anchor=diagnostic.documentation_anchor,
        )
        for diagnostic in compilation.diagnostics
    )
    query_id = uuid4()
    await create_catalogue_query(
        runtime.query_bucket,
        CatalogueQueryState(
            id=query_id,
            statement_kind=statement.kind,
            optimization_status=optimization_status,
            applied_rewrites=applied_rewrites,
            optimization_diagnostics=optimization_diagnostics,
            status="queued",
            created_at=datetime.now(UTC),
        ),
    )
    active = await runtime.prepare(
        query_id=query_id,
        compilation=compilation,
        statement_kind=statement.kind,
    )
    return query_id, statement, active


async def execute_interactive_query(
    runtime: QuackQueryRuntime,
    sql: str,
    *,
    compiler: AtlasCompiler | None = None,
    compilation: CompilationResult | None = None,
    purpose: InteractiveQueryPurpose | None = None,
) -> BufferedCatalogueResult:
    """Execute and decode one bounded query for a non-Arrow consumer such as an agent."""

    query_id, _statement, active = await prepare_interactive_query(
        runtime,
        sql,
        compiler=compiler,
        compilation=compilation,
        purpose=purpose,
    )
    chunks = [chunk async for chunk in active.stream()]
    state = await get_catalogue_query(runtime.query_bucket, query_id)
    if state is None or state.status != "succeeded":
        raise CatalogueQueryExecutionError(
            state.error if state is not None and state.error else "Catalogue query failed."
        )
    reader = pa.ipc.open_stream(BytesIO(b"".join(chunks)))
    columns = [field.name for field in reader.schema]
    column_types = [str(field.type) for field in reader.schema]
    rows: list[list[Any]] = []
    for batch in reader:
        for record in batch.to_pylist():
            rows.append(
                [
                    to_jsonable_python(
                        record[column],
                        bytes_mode="base64",
                        serialize_unknown=True,
                        fallback=str,
                    )
                    for column in columns
                ]
            )
    return BufferedCatalogueResult(
        query_id=query_id,
        columns=columns,
        column_types=column_types,
        rows=rows,
    )
