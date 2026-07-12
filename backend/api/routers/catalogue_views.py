from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from control.catalogue_views.schemas import (
    CatalogueViewAdopt,
    CatalogueViewCreate,
    CatalogueViewListResponse,
    CatalogueViewRecord,
    CatalogueViewUpdate,
)
from control.catalogue_views.service import (
    adopt_reference,
    create_reference,
    detach_reference,
    drop_referenced_view,
    get_record,
    get_reference,
    list_records,
    recover_source_change,
    update_reference,
)
from db.session import get_session
from repository.catalogue import Catalogue, catalogue_from_env
from repository.catalogue.query import CatalogueQueryError
from repository.catalogue.views import CatalogueViewConflictError, CatalogueViewError, CatalogueViewStore

router = APIRouter(prefix="/catalogue/views", tags=["catalogue-views"])


def _catalogue() -> Catalogue:
    return catalogue_from_env()


@router.get("/", response_model=CatalogueViewListResponse)
def list_(session: Annotated[Session, Depends(get_session)]) -> CatalogueViewListResponse:
    with _catalogue() as catalogue:
        return CatalogueViewListResponse(items=list_records(session, CatalogueViewStore(catalogue)))


@router.get("/{reference_id}", response_model=CatalogueViewRecord)
def get(reference_id: UUID, session: Annotated[Session, Depends(get_session)]) -> CatalogueViewRecord:
    with _catalogue() as catalogue:
        record = get_record(session, CatalogueViewStore(catalogue), reference_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Catalogue view reference not found.")
    return record


@router.post("/", response_model=CatalogueViewRecord, status_code=201)
def create(payload: CatalogueViewCreate, session: Annotated[Session, Depends(get_session)]) -> CatalogueViewRecord:
    try:
        with _catalogue() as catalogue:
            return create_reference(
                session,
                CatalogueViewStore(catalogue),
                name=payload.name,
                sql=payload.sql,
                display_name=payload.display_name,
                description=payload.description,
                created_from_query_revision_id=payload.created_from_query_revision_id,
            )
    except (CatalogueViewError, CatalogueQueryError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


@router.post("/adopt", response_model=CatalogueViewRecord, status_code=201)
def adopt(payload: CatalogueViewAdopt, session: Annotated[Session, Depends(get_session)]) -> CatalogueViewRecord:
    try:
        with _catalogue() as catalogue:
            return adopt_reference(
                session,
                CatalogueViewStore(catalogue),
                view_uuid=payload.ducklake_view_uuid,
                display_name=payload.display_name,
                description=payload.description,
            )
    except (CatalogueViewError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


@router.put("/{reference_id}", response_model=CatalogueViewRecord)
def update(reference_id: UUID, payload: CatalogueViewUpdate, session: Annotated[Session, Depends(get_session)]) -> CatalogueViewRecord:
    reference = get_reference(session, reference_id)
    if reference is None:
        raise HTTPException(status_code=404, detail="Catalogue view reference not found.")
    try:
        with _catalogue() as catalogue:
            return update_reference(
                session,
                CatalogueViewStore(catalogue),
                reference,
                expected_uuid=payload.expected_ducklake_view_uuid,
                sql=payload.sql,
                display_name=payload.display_name,
                description=payload.description,
            )
    except (CatalogueViewError, CatalogueQueryError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


@router.post(
    "/{reference_id}/recover-source-change",
    response_model=CatalogueViewRecord,
)
def recover_source_change_(
    reference_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueViewRecord:
    reference = get_reference(session, reference_id)
    if reference is None:
        raise HTTPException(status_code=404, detail="Catalogue view reference not found.")
    try:
        with _catalogue() as catalogue:
            return recover_source_change(
                session,
                CatalogueViewStore(catalogue),
                reference,
            )
    except (CatalogueViewError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


@router.delete("/{reference_id}/reference", status_code=204)
def detach(reference_id: UUID, session: Annotated[Session, Depends(get_session)]) -> None:
    reference = get_reference(session, reference_id)
    if reference is None:
        raise HTTPException(status_code=404, detail="Catalogue view reference not found.")
    try:
        detach_reference(session, reference)
    except CatalogueViewConflictError as exc:
        _raise_mutation_error(exc)


@router.delete("/{reference_id}/object", status_code=204)
def drop(
    reference_id: UUID,
    expected_ducklake_view_uuid: Annotated[UUID, Query()],
    session: Annotated[Session, Depends(get_session)],
) -> None:
    reference = get_reference(session, reference_id)
    if reference is None:
        raise HTTPException(status_code=404, detail="Catalogue view reference not found.")
    try:
        with _catalogue() as catalogue:
            drop_referenced_view(
                session,
                CatalogueViewStore(catalogue),
                reference,
                expected_uuid=expected_ducklake_view_uuid,
            )
    except (CatalogueViewError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


def _raise_mutation_error(exc: Exception) -> NoReturn:
    status = 409 if isinstance(exc, CatalogueViewConflictError) else 422
    raise HTTPException(status_code=status, detail=str(exc)) from exc
