"""Low-level analytical access to the DuckLake catalogue."""

from enum import StrEnum

import duckdb
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.catalogue_pool import CatalogueReadPool, CatalogueReadPoolExhausted
from repository.catalogue.metadata import (
    CatalogueMetadataLimitError,
    read_catalogue_metadata,
)
from repository.catalogue.query import (
    CatalogueQueryError,
    classify_select,
    execute_arrow_query,
    explain_arrow_query,
    lint_select,
    stream_arrow_reader,
)
from repository.catalogue.status import read_catalogue_status

router = APIRouter(prefix="/catalogue", tags=["catalogue"])


class CatalogueQueryMode(StrEnum):
    RUN = "run"
    EXPLAIN = "explain"
    EXPLAIN_ANALYZE = "explain_analyze"


class CatalogueSqlRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)
    mode: CatalogueQueryMode = CatalogueQueryMode.RUN


class CatalogueLintDiagnosticResponse(BaseModel):
    code: str
    severity: str
    message: str


class CatalogueLintResponse(BaseModel):
    diagnostics: list[CatalogueLintDiagnosticResponse]


class CatalogueStatusResponse(BaseModel):
    active_file_count: int
    active_storage_bytes: int
    ducklake_version: str | None
    catalogue_schema_version: int


class CatalogueMetadataColumnResponse(BaseModel):
    name: str
    data_type: str
    nullable: bool


class CatalogueMetadataRelationResponse(BaseModel):
    catalog_name: str
    schema_name: str
    name: str
    kind: str
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


@router.post("/sql/lint", response_model=CatalogueLintResponse)
def lint_sql(payload: CatalogueSqlRequest) -> CatalogueLintResponse:
    return CatalogueLintResponse(
        diagnostics=[
            CatalogueLintDiagnosticResponse(
                code=item.code,
                severity=item.severity,
                message=item.message,
            )
            for item in lint_select(payload.sql)
        ]
    )


@router.get("/status", response_model=CatalogueStatusResponse)
def catalogue_status(request: Request) -> CatalogueStatusResponse:
    pool: CatalogueReadPool = request.app.state.catalogue_read_pool
    try:
        catalogue = pool.acquire()
    except CatalogueReadPoolExhausted as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        status = read_catalogue_status(catalogue)
    finally:
        pool.release(catalogue)

    return CatalogueStatusResponse(
        active_file_count=status.active_file_count,
        active_storage_bytes=status.active_storage_bytes,
        ducklake_version=status.ducklake_version,
        catalogue_schema_version=status.catalogue_schema_version,
    )


@router.get("/metadata", response_model=CatalogueMetadataResponse)
def catalogue_metadata(request: Request) -> CatalogueMetadataResponse:
    pool: CatalogueReadPool = request.app.state.catalogue_read_pool
    try:
        catalogue = pool.acquire()
    except CatalogueReadPoolExhausted as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        metadata = read_catalogue_metadata(catalogue)
    except CatalogueMetadataLimitError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        pool.release(catalogue)

    return CatalogueMetadataResponse(
        catalog_name=metadata.catalog_name,
        default_schema=metadata.default_schema,
        relations=[
            CatalogueMetadataRelationResponse(
                catalog_name=relation.catalog_name,
                schema_name=relation.schema_name,
                name=relation.name,
                kind=relation.kind,
                columns=[
                    CatalogueMetadataColumnResponse(
                        name=column.name,
                        data_type=column.data_type,
                        nullable=column.nullable,
                    )
                    for column in relation.columns
                ],
            )
            for relation in metadata.relations
        ],
        functions=[
            CatalogueMetadataFunctionResponse(
                catalog_name=function.catalog_name,
                schema_name=function.schema_name,
                name=function.name,
                kind=function.kind,
                description=function.description,
                return_type=function.return_type,
                parameters=[
                    CatalogueMetadataFunctionParameterResponse(
                        name=parameter.name,
                        data_type=parameter.data_type,
                    )
                    for parameter in function.parameters
                ],
                varargs=function.varargs,
                result_columns=[
                    CatalogueMetadataColumnResponse(
                        name=column.name,
                        data_type=column.data_type,
                        nullable=column.nullable,
                    )
                    for column in function.result_columns
                ],
            )
            for function in metadata.functions
        ],
    )


@router.post("/sql", response_class=StreamingResponse)
def sql_query(payload: CatalogueSqlRequest, request: Request) -> StreamingResponse:
    try:
        classify_select(payload.sql)
    except CatalogueQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    pool: CatalogueReadPool = request.app.state.catalogue_read_pool
    try:
        catalogue = pool.acquire()
    except CatalogueReadPoolExhausted as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        if payload.mode == CatalogueQueryMode.RUN:
            reader = execute_arrow_query(catalogue, payload.sql)
        else:
            reader = explain_arrow_query(
                catalogue,
                payload.sql,
                analyze=payload.mode == CatalogueQueryMode.EXPLAIN_ANALYZE,
            )
    except (CatalogueQueryError, duckdb.Error) as exc:
        pool.release(catalogue)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        pool.release(catalogue)
        raise

    def body():
        try:
            yield from stream_arrow_reader(reader)
        finally:
            pool.release(catalogue)

    return StreamingResponse(
        body(),
        media_type="application/vnd.apache.arrow.stream",
        headers={"Content-Disposition": 'inline; filename="catalogue.arrow"'},
    )
