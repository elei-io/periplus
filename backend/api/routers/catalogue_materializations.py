from typing import Annotated, NoReturn
from uuid import UUID

import duckdb
from fastapi import APIRouter, Depends, HTTPException

from api.catalogue_control import CatalogueControl, get_catalogue_control
from control.catalogue_materializations.schemas import (
    CatalogueMaterializationListResponse,
    CatalogueMaterializationStateUpdate,
    CatalogueMaterializationRecord,
    ViewMaterializationEligibility,
    ViewMaterializationEligibilityRequest,
    ViewMaterializationPut,
)
from control.catalogue_materializations.service import (
    get_model,
    list_records,
    materialization_eligibility,
    materialization_store,
    put_for_view,
    record,
    request_dematerialization,
    update_state,
)
from repository.catalogue.materializations import (
    MaterializationConflictError,
    MaterializationError,
)
from repository.catalogue.query import CatalogueQueryError

router = APIRouter(tags=["catalogue-materializations"])


@router.get(
    "/catalogue/materializations/",
    response_model=CatalogueMaterializationListResponse,
)
async def list_(
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueMaterializationListResponse:
    items = await control.run(lambda session, _catalogue: list_records(session))
    return CatalogueMaterializationListResponse(items=items, total=len(items))


@router.get(
    "/catalogue/materializations/{materialization_id}",
    response_model=CatalogueMaterializationRecord,
)
async def get(
    materialization_id: UUID,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueMaterializationRecord:
    def operation(session, _catalogue):
        model = get_model(session, materialization_id)
        if model is None:
            raise LookupError("Catalogue materialization not found.")
        return record(session, model)

    try:
        return await control.run(operation)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put(
    "/catalogue/views/{view_reference_id}/materialization",
    response_model=CatalogueMaterializationRecord,
)
async def materialize_view(
    view_reference_id: UUID,
    payload: ViewMaterializationPut,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueMaterializationRecord:
    def operation(session, catalogue):
        return put_for_view(
            session,
            materialization_store(session, catalogue),
            view_reference_id=view_reference_id,
            **payload.model_dump(),
        )

    try:
        return await control.run(operation)
    except (
        MaterializationError,
        CatalogueQueryError,
        duckdb.Error,
        LookupError,
    ) as exc:
        _raise(exc)


@router.post(
    "/catalogue/views/{view_reference_id}/materialization-eligibility",
    response_model=ViewMaterializationEligibility,
)
async def materialization_eligibility_for_view(
    view_reference_id: UUID,
    payload: ViewMaterializationEligibilityRequest,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> ViewMaterializationEligibility:
    def operation(session, catalogue):
        return materialization_eligibility(
            session,
            materialization_store(session, catalogue),
            view_reference_id=view_reference_id,
            **payload.model_dump(),
        )

    try:
        return await control.run(operation)
    except (CatalogueQueryError, duckdb.Error, LookupError) as exc:
        _raise(exc)


@router.patch(
    "/catalogue/materializations/{materialization_id}",
    response_model=CatalogueMaterializationRecord,
)
async def update(
    materialization_id: UUID,
    payload: CatalogueMaterializationStateUpdate,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueMaterializationRecord:
    def operation(session, _catalogue):
        model = get_model(session, materialization_id)
        if model is None:
            raise LookupError("Catalogue materialization not found.")
        return update_state(session, model, **payload.model_dump())

    try:
        return await control.run(operation)
    except (MaterializationError, LookupError) as exc:
        _raise(exc)


@router.delete(
    "/catalogue/materializations/{materialization_id}",
    response_model=CatalogueMaterializationRecord,
)
async def dematerialize(
    materialization_id: UUID,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueMaterializationRecord:
    def operation(session, _catalogue):
        model = get_model(session, materialization_id)
        if model is None:
            raise LookupError("Catalogue materialization not found.")
        return request_dematerialization(session, model)

    try:
        return await control.run(operation)
    except (MaterializationError, LookupError) as exc:
        _raise(exc)


def _raise(exc: Exception) -> NoReturn:
    if isinstance(exc, LookupError):
        status = 404
    elif isinstance(exc, MaterializationConflictError):
        status = 409
    else:
        status = 422
    raise HTTPException(status_code=status, detail=str(exc)) from exc
