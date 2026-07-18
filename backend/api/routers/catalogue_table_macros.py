from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from api.catalogue_control import CatalogueControl, get_catalogue_control
from control.catalogue_table_macros.schemas import (
    CatalogueTableMacroCreate,
    CatalogueTableMacroListResponse,
    CatalogueTableMacroRecord,
    CatalogueTableMacroUpdate,
)
from control.catalogue_table_macros.service import (
    create_definition,
    drop_definition,
    get_definition,
    get_record,
    list_records,
    update_definition,
)
from repository.catalogue.operations import operation_lock
from repository.catalogue.query import CatalogueQueryError
from repository.catalogue.table_macros import (
    CatalogueTableMacroConflictError,
    CatalogueTableMacroError,
    CatalogueTableMacroStore,
)

router = APIRouter(prefix="/catalogue/macros", tags=["catalogue-table-macros"])


@router.get("/", response_model=CatalogueTableMacroListResponse)
async def list_(
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueTableMacroListResponse:
    items = await control.run(
        lambda session, catalogue: list_records(
            session, CatalogueTableMacroStore(catalogue)
        )
    )
    return CatalogueTableMacroListResponse(items=items)


@router.get("/{definition_id}", response_model=CatalogueTableMacroRecord)
async def get(
    definition_id: UUID,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueTableMacroRecord:
    record = await control.run(
        lambda session, catalogue: get_record(
            session, CatalogueTableMacroStore(catalogue), definition_id
        )
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Table macro not found.")
    return record


@router.post("/", response_model=CatalogueTableMacroRecord, status_code=201)
async def create(
    payload: CatalogueTableMacroCreate,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueTableMacroRecord:
    try:

        def operation(session, catalogue):
            with operation_lock(
                catalogue, f"catalogue-table-macro-create:{payload.slug}"
            ):
                return create_definition(
                    session,
                    CatalogueTableMacroStore(catalogue),
                    **payload.model_dump(),
                )

        return await control.run(operation)
    except (CatalogueTableMacroError, CatalogueQueryError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


@router.put("/{definition_id}", response_model=CatalogueTableMacroRecord)
async def update(
    definition_id: UUID,
    payload: CatalogueTableMacroUpdate,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> CatalogueTableMacroRecord:
    try:

        def operation(session, catalogue):
            definition = get_definition(session, definition_id)
            if definition is None:
                raise HTTPException(status_code=404, detail="Table macro not found.")
            if definition.fixture_path is not None:
                raise HTTPException(
                    status_code=409, detail="System table macros are read-only."
                )
            with operation_lock(
                catalogue, f"catalogue-table-macro-update:{definition_id}"
            ):
                return update_definition(
                    session,
                    CatalogueTableMacroStore(catalogue),
                    definition,
                    expected_revision_id=payload.expected_definition_revision_id,
                    parameters=payload.parameters,
                    parameter_defaults=payload.parameter_defaults,
                    sql=payload.sql,
                    slug=payload.slug,
                    description=payload.description,
                )

        return await control.run(operation)
    except (CatalogueTableMacroError, CatalogueQueryError, duckdb.Error) as exc:
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
                raise HTTPException(status_code=404, detail="Table macro not found.")
            if definition.fixture_path is not None:
                raise HTTPException(
                    status_code=409, detail="System table macros cannot be deleted."
                )
            with operation_lock(
                catalogue, f"catalogue-table-macro-drop:{definition_id}"
            ):
                drop_definition(
                    session,
                    CatalogueTableMacroStore(catalogue),
                    definition,
                    expected_revision_id=expected_definition_revision_id,
                )

        await control.run(operation)
    except (CatalogueTableMacroError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


def _raise_mutation_error(exc: Exception) -> NoReturn:
    status = 409 if isinstance(exc, CatalogueTableMacroConflictError) else 422
    raise HTTPException(status_code=status, detail=str(exc)) from exc
