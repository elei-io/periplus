from typing import Annotated, NoReturn
from uuid import UUID

import duckdb
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from control.materialized_views.schemas import MaterializedViewCreate, MaterializedViewListResponse, MaterializedViewRecord, MaterializedViewRefresh
from control.materialized_views.service import create, drop, get_model, list_records, record, refresh
from db.session import get_session
from repository.catalogue import Catalogue
from repository.catalogue.config import catalogue_config_from_env
from repository.catalogue.materialized_views import MaterializedViewConflictError, MaterializedViewError, MaterializedViewStore
from repository.catalogue.query import CatalogueQueryError

router = APIRouter(prefix="/materialized-views", tags=["materialized-views"])


def _catalogue() -> Catalogue:
    return Catalogue(catalogue_config_from_env())


@router.get("/", response_model=MaterializedViewListResponse)
def list_(session: Annotated[Session, Depends(get_session)]) -> MaterializedViewListResponse:
    with _catalogue() as catalogue:
        items = list_records(session, MaterializedViewStore(catalogue))
    return MaterializedViewListResponse(items=items, total=len(items))


@router.get("/{view_id}", response_model=MaterializedViewRecord)
def get(view_id: UUID, session: Annotated[Session, Depends(get_session)]) -> MaterializedViewRecord:
    model = get_model(session, view_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Materialized view not found.")
    with _catalogue() as catalogue:
        return record(session, MaterializedViewStore(catalogue), model)


@router.post("/", response_model=MaterializedViewRecord, status_code=201)
def create_(payload: MaterializedViewCreate, session: Annotated[Session, Depends(get_session)]) -> MaterializedViewRecord:
    try:
        with _catalogue() as catalogue:
            return create(session, MaterializedViewStore(catalogue), **payload.model_dump())
    except (MaterializedViewError, CatalogueQueryError, duckdb.Error, LookupError) as exc:
        _raise(exc)


@router.post("/{view_id}/refresh", response_model=MaterializedViewRecord)
def refresh_(view_id: UUID, payload: MaterializedViewRefresh, session: Annotated[Session, Depends(get_session)]) -> MaterializedViewRecord:
    model = get_model(session, view_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Materialized view not found.")
    try:
        with _catalogue() as catalogue:
            return refresh(session, MaterializedViewStore(catalogue), model, expected_uuid=payload.expected_ducklake_table_uuid)
    except (MaterializedViewError, CatalogueQueryError, duckdb.Error, LookupError) as exc:
        _raise(exc)


@router.delete("/{view_id}", status_code=204)
def drop_(view_id: UUID, expected_ducklake_table_uuid: UUID, session: Annotated[Session, Depends(get_session)]) -> None:
    model = get_model(session, view_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Materialized view not found.")
    try:
        with _catalogue() as catalogue:
            drop(session, MaterializedViewStore(catalogue), model, expected_uuid=expected_ducklake_table_uuid)
    except (MaterializedViewError, duckdb.Error) as exc:
        _raise(exc)


def _raise(exc: Exception) -> NoReturn:
    raise HTTPException(status_code=409 if isinstance(exc, MaterializedViewConflictError) else 422, detail=str(exc)) from exc
