"""Browser catalogue runtime configuration and SQL validation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, HTTPException

from repository.catalogue.query import (
    CatalogueQueryError,
    CatalogueStatementKind,
    classify_catalogue_statement,
    lint_catalogue_statement,
)

router = APIRouter(prefix="/catalogue", tags=["catalogue"])


class CatalogueSqlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=100_000)


class CatalogueLintDiagnosticResponse(BaseModel):
    code: str
    severity: str
    message: str


class CatalogueLintResponse(BaseModel):
    diagnostics: list[CatalogueLintDiagnosticResponse]


class CatalogueQueryRuntimeResponse(BaseModel):
    transport: Literal["quack"] = "quack"
    quack_uri: str
    quack_token: str
    catalogue_alias: str
    catalogue_schema: str
    metadata_schema: str
    catalogue_schema_version: str
    setup_sql: list[str] = Field(default_factory=list)
    attach_sql: str


class CataloguePreparedSqlResponse(BaseModel):
    sql: str
    statement_kind: CatalogueStatementKind


@router.get("/query-runtime", response_model=CatalogueQueryRuntimeResponse)
def query_runtime() -> CatalogueQueryRuntimeResponse:
    from repository.catalogue.browser_runtime import (
        browser_quack_runtime_from_env,
    )

    runtime = browser_quack_runtime_from_env()
    return CatalogueQueryRuntimeResponse(
        quack_uri=runtime.uri,
        quack_token=runtime.token,
        catalogue_alias=runtime.catalogue_alias,
        catalogue_schema=runtime.catalogue_schema,
        metadata_schema=runtime.metadata_schema,
        catalogue_schema_version=runtime.catalogue_schema_version,
        setup_sql=list(runtime.setup_sql),
        attach_sql=runtime.attach_sql,
    )


@router.post("/sql/prepare", response_model=CataloguePreparedSqlResponse)
def prepare_sql(payload: CatalogueSqlRequest) -> CataloguePreparedSqlResponse:
    try:
        statement = classify_catalogue_statement(payload.sql)
    except CatalogueQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CataloguePreparedSqlResponse(
        sql=statement.sql,
        statement_kind=statement.kind,
    )


@router.post("/sql/lint", response_model=CatalogueLintResponse)
def lint_sql(payload: CatalogueSqlRequest) -> CatalogueLintResponse:
    return CatalogueLintResponse(
        diagnostics=[
            CatalogueLintDiagnosticResponse(
                code=item.code,
                severity=item.severity,
                message=item.message,
            )
            for item in lint_catalogue_statement(payload.sql)
        ]
    )
