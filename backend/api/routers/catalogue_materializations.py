from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, NoReturn
from uuid import UUID

import duckdb
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from control.catalogue_materializations.schemas import (
    CatalogueMaterializationListResponse,
    CatalogueMaterializationMaintenanceUpdate,
    CatalogueMaterializationRebuild,
    CatalogueMaterializationRecord,
    QueryMaterializationPut,
    ViewMaterializationPut,
)
from control.catalogue_materializations.service import (
    get_model,
    list_records,
    put_for_query,
    put_for_view,
    rebuild,
    record,
    request_dematerialization,
    update_maintenance,
)
from db.session import get_session
from repository.catalogue import Catalogue, catalogue_from_env
from repository.catalogue.materializations import (
    MaterializationConflictError,
    MaterializationError,
    MaterializationStore,
)
from repository.catalogue.query import CatalogueQueryError
from repository.catalogue.operations import operation_lock

router = APIRouter(tags=["catalogue-materializations"])


def _catalogue() -> Catalogue:
    return catalogue_from_env()


@contextmanager
def _catalogue_mutation(operation_id: str) -> Iterator[Catalogue]:
    with operation_lock(operation_id), _catalogue() as catalogue:
        yield catalogue


@router.get(
    "/catalogue/materializations/",
    response_model=CatalogueMaterializationListResponse,
)
def list_(
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueMaterializationListResponse:
    with _catalogue() as catalogue:
        items = list_records(session, MaterializationStore(catalogue))
    return CatalogueMaterializationListResponse(items=items, total=len(items))


@router.get(
    "/catalogue/materializations/{materialization_id}",
    response_model=CatalogueMaterializationRecord,
)
def get(
    materialization_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueMaterializationRecord:
    model = get_model(session, materialization_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Catalogue materialization not found.")
    with _catalogue() as catalogue:
        return record(session, MaterializationStore(catalogue), model)


@router.put(
    "/catalogue/queries/{query_id}/materialization",
    response_model=CatalogueMaterializationRecord,
)
def materialize_query(
    query_id: UUID,
    payload: QueryMaterializationPut,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueMaterializationRecord:
    try:
        with _catalogue_mutation(f"materialize-query:{query_id}") as catalogue:
            return put_for_query(
                session,
                MaterializationStore(catalogue),
                query_id=query_id,
                **payload.model_dump(),
            )
    except (MaterializationError, CatalogueQueryError, duckdb.Error, LookupError) as exc:
        _raise(exc)


@router.put(
    "/catalogue/views/{view_reference_id}/materialization",
    response_model=CatalogueMaterializationRecord,
)
def materialize_view(
    view_reference_id: UUID,
    payload: ViewMaterializationPut,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueMaterializationRecord:
    try:
        with _catalogue_mutation(f"materialize-view:{view_reference_id}") as catalogue:
            return put_for_view(
                session,
                MaterializationStore(catalogue),
                view_reference_id=view_reference_id,
                **payload.model_dump(),
            )
    except (MaterializationError, CatalogueQueryError, duckdb.Error, LookupError) as exc:
        _raise(exc)


@router.post(
    "/catalogue/materializations/{materialization_id}/rebuild",
    response_model=CatalogueMaterializationRecord,
)
def rebuild_(
    materialization_id: UUID,
    payload: CatalogueMaterializationRebuild,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueMaterializationRecord:
    model = get_model(session, materialization_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Catalogue materialization not found.")
    try:
        with _catalogue_mutation(f"materialization-rebuild:{materialization_id}") as catalogue:
            return rebuild(
                session,
                MaterializationStore(catalogue),
                model,
                expected_uuid=payload.expected_ducklake_table_uuid,
                target_query_revision_id=payload.target_query_revision_id,
            )
    except (MaterializationError, CatalogueQueryError, duckdb.Error, LookupError) as exc:
        _raise(exc)


@router.patch(
    "/catalogue/materializations/{materialization_id}/maintenance",
    response_model=CatalogueMaterializationRecord,
)
def maintenance(
    materialization_id: UUID,
    payload: CatalogueMaterializationMaintenanceUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueMaterializationRecord:
    model = get_model(session, materialization_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Catalogue materialization not found.")
    try:
        with _catalogue_mutation(f"materialization-maintenance:{materialization_id}") as catalogue:
            return update_maintenance(
                session,
                MaterializationStore(catalogue),
                model,
                **payload.model_dump(),
            )
    except (MaterializationError, duckdb.Error) as exc:
        _raise(exc)


@router.delete(
    "/catalogue/materializations/{materialization_id}",
    response_model=CatalogueMaterializationRecord,
)
def dematerialize(
    materialization_id: UUID,
    expected_ducklake_table_uuid: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueMaterializationRecord:
    model = get_model(session, materialization_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Catalogue materialization not found.")
    try:
        with _catalogue_mutation(f"materialization-dematerialize:{materialization_id}") as catalogue:
            return request_dematerialization(
                session,
                MaterializationStore(catalogue),
                model,
                expected_uuid=expected_ducklake_table_uuid,
            )
    except (MaterializationError, duckdb.Error) as exc:
        _raise(exc)


def _raise(exc: Exception) -> NoReturn:
    status = 409 if isinstance(exc, MaterializationConflictError) else 422
    raise HTTPException(status_code=status, detail=str(exc)) from exc
