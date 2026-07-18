from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, NoReturn
from uuid import UUID

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

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
from db.session import get_session
from repository.catalogue import Catalogue, catalogue_from_env
from repository.catalogue.operations import operation_lock
from repository.catalogue.query import CatalogueQueryError
from repository.catalogue.table_macros import (
    CatalogueTableMacroConflictError,
    CatalogueTableMacroError,
    CatalogueTableMacroStore,
)

router = APIRouter(prefix="/catalogue/macros", tags=["catalogue-table-macros"])


def _catalogue() -> Catalogue:
    return catalogue_from_env()


@contextmanager
def _catalogue_mutation(operation_id: str) -> Iterator[Catalogue]:
    with _catalogue() as catalogue:
        with operation_lock(catalogue, operation_id):
            yield catalogue


@router.get("/", response_model=CatalogueTableMacroListResponse)
def list_(
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueTableMacroListResponse:
    with _catalogue() as catalogue:
        items = list_records(session, CatalogueTableMacroStore(catalogue))
    return CatalogueTableMacroListResponse(items=items)


@router.get("/{definition_id}", response_model=CatalogueTableMacroRecord)
def get(
    definition_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueTableMacroRecord:
    with _catalogue() as catalogue:
        record = get_record(session, CatalogueTableMacroStore(catalogue), definition_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Table macro not found.")
    return record


@router.post("/", response_model=CatalogueTableMacroRecord, status_code=201)
def create(
    payload: CatalogueTableMacroCreate,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueTableMacroRecord:
    try:
        with _catalogue_mutation(f"catalogue-table-macro-create:{payload.slug}") as catalogue:
            return create_definition(
                session, CatalogueTableMacroStore(catalogue), **payload.model_dump()
            )
    except (CatalogueTableMacroError, CatalogueQueryError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


@router.put("/{definition_id}", response_model=CatalogueTableMacroRecord)
def update(
    definition_id: UUID,
    payload: CatalogueTableMacroUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueTableMacroRecord:
    definition = get_definition(session, definition_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Table macro not found.")
    if definition.fixture_path is not None:
        raise HTTPException(status_code=409, detail="System table macros are read-only.")
    try:
        with _catalogue_mutation(f"catalogue-table-macro-update:{definition_id}") as catalogue:
            return update_definition(
                session,
                CatalogueTableMacroStore(catalogue),
                definition,
                expected_revision_id=payload.expected_definition_revision_id,
                parameters=payload.parameters,
                sql=payload.sql,
                slug=payload.slug,
                description=payload.description,
            )
    except (CatalogueTableMacroError, CatalogueQueryError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


@router.delete("/{definition_id}", status_code=204)
def drop(
    definition_id: UUID,
    expected_definition_revision_id: Annotated[UUID, Query()],
    session: Annotated[Session, Depends(get_session)],
) -> None:
    definition = get_definition(session, definition_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Table macro not found.")
    if definition.fixture_path is not None:
        raise HTTPException(
            status_code=409, detail="System table macros cannot be deleted."
        )
    try:
        with _catalogue_mutation(f"catalogue-table-macro-drop:{definition_id}") as catalogue:
            drop_definition(
                session,
                CatalogueTableMacroStore(catalogue),
                definition,
                expected_revision_id=expected_definition_revision_id,
            )
    except (CatalogueTableMacroError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


def _raise_mutation_error(exc: Exception) -> NoReturn:
    status = 409 if isinstance(exc, CatalogueTableMacroConflictError) else 422
    raise HTTPException(status_code=status, detail=str(exc)) from exc
