"""Low-level analytical access to the DuckLake catalogue."""

from enum import StrEnum

import duckdb
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.catalogue_pool import CatalogueReadPool, CatalogueReadPoolExhausted
from repository.catalogue.query import (
    CatalogueQueryError,
    classify_select,
    execute_arrow_query,
    explain_arrow_query,
    lint_select,
    stream_arrow_reader,
)

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
