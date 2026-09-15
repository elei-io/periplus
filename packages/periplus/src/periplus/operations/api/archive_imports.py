"""Administrative archive expansion, separate from collection requests."""
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from periplus.ingestion.imports.control import ImportConflict, ImportControl
from periplus.ingestion.imports.schemas import CreateImport, ImportView

router = APIRouter(prefix="/operations/archive-imports", tags=["operations"])


def control(request: Request) -> ImportControl:
    return ImportControl(request.app.state.frontier_sessions)


@router.post("", response_model=ImportView)
def create(payload: CreateImport, request: Request) -> ImportView:
    try:
        return control(request).create(payload.id, payload.specification)
    except (ImportConflict, IntegrityError) as error:
        raise HTTPException(409, "Import identity already exists; refresh before retrying.") from error


@router.get("", response_model=list[ImportView])
def list_imports(request: Request) -> list[ImportView]:
    return control(request).list()


@router.get("/{identity}", response_model=ImportView)
def get_import(identity: UUID, request: Request) -> ImportView:
    try:
        return control(request).get(identity)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/{identity}/{action}", response_model=ImportView)
def action_import(identity: UUID, action: Literal["retry", "cancel"], request: Request) -> ImportView:
    try:
        return control(request).action(identity, action)
    except ImportConflict as error:
        raise HTTPException(409, str(error)) from error
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
