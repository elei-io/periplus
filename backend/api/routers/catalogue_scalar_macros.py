from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, NoReturn
from uuid import UUID

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

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
from db.session import get_session
from repository.catalogue import Catalogue, catalogue_from_env
from repository.catalogue.operations import operation_lock
from repository.catalogue.scalar_macros import (
    CatalogueScalarMacroConflictError,
    CatalogueScalarMacroError,
    CatalogueScalarMacroStore,
)

router = APIRouter(
    prefix="/catalogue/scalar-macros", tags=["catalogue-scalar-macros"]
)


def _catalogue() -> Catalogue:
    return catalogue_from_env()


@contextmanager
def _catalogue_mutation(operation_id: str) -> Iterator[Catalogue]:
    with _catalogue() as catalogue:
        with operation_lock(catalogue, operation_id):
            yield catalogue


@router.get("/", response_model=CatalogueScalarMacroListResponse)
def list_(
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueScalarMacroListResponse:
    with _catalogue() as catalogue:
        items = list_records(session, CatalogueScalarMacroStore(catalogue))
    return CatalogueScalarMacroListResponse(items=items)


@router.get("/{definition_id}", response_model=CatalogueScalarMacroRecord)
def get(
    definition_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueScalarMacroRecord:
    with _catalogue() as catalogue:
        record = get_record(session, CatalogueScalarMacroStore(catalogue), definition_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Scalar macro not found.")
    return record


@router.post("/", response_model=CatalogueScalarMacroRecord, status_code=201)
def create(
    payload: CatalogueScalarMacroCreate,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueScalarMacroRecord:
    try:
        with _catalogue_mutation(
            f"catalogue-scalar-macro-create:{payload.slug}"
        ) as catalogue:
            return create_definition(
                session, CatalogueScalarMacroStore(catalogue), **payload.model_dump()
            )
    except (CatalogueScalarMacroError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


@router.put("/{definition_id}", response_model=CatalogueScalarMacroRecord)
def update(
    definition_id: UUID,
    payload: CatalogueScalarMacroUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueScalarMacroRecord:
    definition = get_definition(session, definition_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Scalar macro not found.")
    try:
        with _catalogue_mutation(
            f"catalogue-scalar-macro-update:{definition_id}"
        ) as catalogue:
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
    except (CatalogueScalarMacroError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


@router.delete("/{definition_id}", status_code=204)
def drop(
    definition_id: UUID,
    expected_definition_revision_id: Annotated[UUID, Query()],
    session: Annotated[Session, Depends(get_session)],
) -> None:
    definition = get_definition(session, definition_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Scalar macro not found.")
    try:
        with _catalogue_mutation(
            f"catalogue-scalar-macro-drop:{definition_id}"
        ) as catalogue:
            drop_definition(
                session,
                CatalogueScalarMacroStore(catalogue),
                definition,
                expected_revision_id=expected_definition_revision_id,
            )
    except (CatalogueScalarMacroError, duckdb.Error) as exc:
        _raise_mutation_error(exc)


def _raise_mutation_error(exc: Exception) -> NoReturn:
    status = 409 if isinstance(exc, CatalogueScalarMacroConflictError) else 422
    raise HTTPException(status_code=status, detail=str(exc)) from exc
