from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from api.catalogue_control import CatalogueControl, get_catalogue_control
from control.catalogue_scalar_macros.schemas import (
    CatalogueScalarMacroCreate,
    CatalogueScalarMacroListResponse,
    CatalogueScalarMacroRecord,
    CatalogueScalarMacroUpdate,
)
from control.catalogue_scalar_macros.service import (
    create_definition,
    drop_definition,
    get_definition,
    get_record,
    list_records,
    update_definition,
)
from repository.catalogue.scalar_macros import (
    CatalogueScalarMacroConflictError,
    CatalogueScalarMacroError,
    CatalogueScalarMacroStore,
)

router = APIRouter(prefix="/catalogue/scalar-macros", tags=["catalogue-scalar-macros"])


@router.get("/", response_model=CatalogueScalarMacroListResponse)
async def list_(
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueScalarMacroListResponse:
    items = await control.run(
        lambda session, catalogue: list_records(
            session, CatalogueScalarMacroStore(catalogue)
        )
    )
    return CatalogueScalarMacroListResponse(items=items)


@router.get("/{definition_id}", response_model=CatalogueScalarMacroRecord)
async def get(
    definition_id: UUID,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueScalarMacroRecord:
    record = await control.run(
        lambda session, catalogue: get_record(
            session, CatalogueScalarMacroStore(catalogue), definition_id
        )
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Scalar macro not found.")
    return record


@router.post("/", response_model=CatalogueScalarMacroRecord, status_code=201)
async def create(
    payload: CatalogueScalarMacroCreate,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueScalarMacroRecord:
    try:

        def operation(session, catalogue):
            return create_definition(
                session,
                CatalogueScalarMacroStore(catalogue),
                **payload.model_dump(),
            )

        return await control.run(operation)
    except (CatalogueScalarMacroError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


@router.put("/{definition_id}", response_model=CatalogueScalarMacroRecord)
async def update(
    definition_id: UUID,
    payload: CatalogueScalarMacroUpdate,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueScalarMacroRecord:
    try:

        def operation(session, catalogue):
            definition = get_definition(session, definition_id)
            if definition is None:
                raise HTTPException(status_code=404, detail="Scalar macro not found.")
            return update_definition(
                session,
                CatalogueScalarMacroStore(catalogue),
                definition,
                expected_revision_id=payload.expected_definition_revision_id,
                parameters=payload.parameters,
                sql=payload.sql,
                slug=payload.slug,
                description=payload.description,
            )

        return await control.run(operation)
    except (CatalogueScalarMacroError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


@router.delete("/{definition_id}", status_code=204)
async def drop(
    definition_id: UUID,
    expected_definition_revision_id: Annotated[UUID, Query()],
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> None:
    try:

        def operation(session, catalogue):
            definition = get_definition(session, definition_id)
            if definition is None:
                raise HTTPException(status_code=404, detail="Scalar macro not found.")
            drop_definition(
                session,
                CatalogueScalarMacroStore(catalogue),
                definition,
                expected_revision_id=expected_definition_revision_id,
            )

        await control.run(operation)
    except (CatalogueScalarMacroError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


def _raise_mutation_error(exc: Exception) -> NoReturn:
    status = 409 if isinstance(exc, CatalogueScalarMacroConflictError) else 422
    raise HTTPException(status_code=status, detail=str(exc)) from exc
