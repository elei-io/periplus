from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from api.catalogue_control import CatalogueControl, get_catalogue_control
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
    update_reference,
)
from repository.catalogue.query import CatalogueQueryError
from repository.catalogue.operations import operation_lock
from repository.catalogue.views import (
    CatalogueViewConflictError,
    CatalogueViewError,
    CatalogueViewStore,
)

router = APIRouter(prefix="/catalogue/views", tags=["catalogue-views"])


@router.get("/", response_model=CatalogueViewListResponse)
async def list_(
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueViewListResponse:
    return await control.run(
        lambda session, catalogue: CatalogueViewListResponse(
            items=list_records(session, CatalogueViewStore(catalogue))
        )
    )


@router.get("/{reference_id}", response_model=CatalogueViewRecord)
async def get(
    reference_id: UUID,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueViewRecord:
    record = await control.run(
        lambda session, catalogue: get_record(
            session, CatalogueViewStore(catalogue), reference_id
        )
    )
    if record is None:
        raise HTTPException(
            status_code=404, detail="Catalogue view reference not found."
        )
    return record


@router.post("/", response_model=CatalogueViewRecord, status_code=201)
async def create(
    payload: CatalogueViewCreate,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueViewRecord:
    try:

        def operation(session, catalogue):
            with operation_lock(catalogue, f"catalogue-view-create:{payload.slug}"):
                return create_reference(
                    session,
                    CatalogueViewStore(catalogue),
                    slug=payload.slug,
                    sql=payload.sql,
                    description=payload.description,
                    created_from_query_revision_id=payload.created_from_query_revision_id,
                )

        return await control.run(operation)
    except (CatalogueViewError, CatalogueQueryError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


@router.post("/adopt", response_model=CatalogueViewRecord, status_code=201)
async def adopt(
    payload: CatalogueViewAdopt,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueViewRecord:
    try:

        def operation(session, catalogue):
            with operation_lock(
                catalogue,
                f"catalogue-view-adopt:{payload.ducklake_view_uuid}",
            ):
                return adopt_reference(
                    session,
                    CatalogueViewStore(catalogue),
                    view_uuid=payload.ducklake_view_uuid,
                    slug=payload.slug,
                    description=payload.description,
                )

        return await control.run(operation)
    except (CatalogueViewError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


@router.put("/{reference_id}", response_model=CatalogueViewRecord)
async def update(
    reference_id: UUID,
    payload: CatalogueViewUpdate,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueViewRecord:
    try:

        def operation(session, catalogue):
            reference = get_reference(session, reference_id)
            if reference is None:
                raise HTTPException(
                    status_code=404, detail="Catalogue view reference not found."
                )
            with operation_lock(catalogue, f"catalogue-view-update:{reference_id}"):
                return update_reference(
                    session,
                    CatalogueViewStore(catalogue),
                    reference,
                    expected_uuid=payload.expected_ducklake_view_uuid,
                    sql=payload.sql,
                    slug=payload.slug,
                    description=payload.description,
                )

        return await control.run(operation)
    except (CatalogueViewError, CatalogueQueryError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


@router.delete("/{reference_id}/reference", status_code=204)
async def detach(
    reference_id: UUID,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> None:
    try:

        def operation(session, _catalogue):
            reference = get_reference(session, reference_id)
            if reference is None:
                raise HTTPException(
                    status_code=404, detail="Catalogue view reference not found."
                )
            detach_reference(session, reference)

        await control.run(operation)
    except CatalogueViewConflictError as exc:
        _raise_mutation_error(exc)


@router.delete("/{reference_id}/object", status_code=204)
async def drop(
    reference_id: UUID,
    expected_ducklake_view_uuid: Annotated[UUID, Query()],
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> None:
    try:

        def operation(session, catalogue):
            reference = get_reference(session, reference_id)
            if reference is None:
                raise HTTPException(
                    status_code=404, detail="Catalogue view reference not found."
                )
            with operation_lock(catalogue, f"catalogue-view-drop:{reference_id}"):
                drop_referenced_view(
                    session,
                    CatalogueViewStore(catalogue),
                    reference,
                    expected_uuid=expected_ducklake_view_uuid,
                )

        await control.run(operation)
    except (CatalogueViewError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


def _raise_mutation_error(exc: Exception) -> NoReturn:
    status = 409 if isinstance(exc, CatalogueViewConflictError) else 422
    raise HTTPException(status_code=status, detail=str(exc)) from exc
