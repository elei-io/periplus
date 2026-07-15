from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from control.catalogue_queries.schemas import (
    CatalogueQueryCreate,
    CatalogueQueryDetail,
    CatalogueQueryListResponse,
    CatalogueQueryRestore,
    CatalogueQueryUpdate,
)
from control.catalogue_queries.service import (
    CatalogueQueryConflictError,
    archive_query,
    create_query,
    detail,
    get_query,
    get_revision,
    list_queries,
    restore_query,
    restore_revision,
    update_query,
)
from db.session import get_session
from repository.catalogue.query import CatalogueQueryError

router = APIRouter(prefix="/catalogue/queries", tags=["catalogue-queries"])


@router.get("/", response_model=CatalogueQueryListResponse)
def list_(
    session: Annotated[Session, Depends(get_session)],
    archived: Annotated[bool, Query()] = False,
) -> CatalogueQueryListResponse:
    items = list_queries(session, archived=archived)
    return CatalogueQueryListResponse(items=items, total=len(items))


@router.post("/", response_model=CatalogueQueryDetail, status_code=201)
def create(
    payload: CatalogueQueryCreate,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueQueryDetail:
    try:
        return create_query(session, **payload.model_dump())
    except CatalogueQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{query_id}", response_model=CatalogueQueryDetail)
def get(
    query_id: UUID, session: Annotated[Session, Depends(get_session)]
) -> CatalogueQueryDetail:
    query = get_query(session, query_id)
    if query is None:
        raise HTTPException(status_code=404, detail="Saved query not found.")
    return detail(query)


@router.put("/{query_id}", response_model=CatalogueQueryDetail)
def update(
    query_id: UUID,
    payload: CatalogueQueryUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueQueryDetail:
    query = get_query(session, query_id)
    if query is None:
        raise HTTPException(status_code=404, detail="Saved query not found.")
    try:
        updated = update_query(
            session,
            query,
            expected_revision_id=payload.expected_current_revision_id,
            sql=payload.sql,
            name=payload.name,
            description=payload.description,
            change_note=payload.change_note,
        )
        return updated
    except CatalogueQueryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CatalogueQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{query_id}/revisions/{revision_id}/restore", response_model=CatalogueQueryDetail)
def restore_revision_(
    query_id: UUID,
    revision_id: UUID,
    payload: CatalogueQueryRestore,
    session: Annotated[Session, Depends(get_session)],
) -> CatalogueQueryDetail:
    query = get_query(session, query_id)
    revision = get_revision(session, query_id, revision_id)
    if query is None or revision is None:
        raise HTTPException(status_code=404, detail="Saved query revision not found.")
    try:
        restored = restore_revision(
            session,
            query,
            revision,
            expected_revision_id=payload.expected_current_revision_id,
            change_note=payload.change_note,
        )
        return restored
    except CatalogueQueryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{query_id}", status_code=204)
def archive(
    query_id: UUID, session: Annotated[Session, Depends(get_session)]
) -> None:
    query = get_query(session, query_id)
    if query is None:
        raise HTTPException(status_code=404, detail="Saved query not found.")
    try:
        archive_query(session, query)
    except CatalogueQueryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{query_id}/restore", response_model=CatalogueQueryDetail)
def restore(
    query_id: UUID, session: Annotated[Session, Depends(get_session)]
) -> CatalogueQueryDetail:
    query = get_query(session, query_id)
    if query is None:
        raise HTTPException(status_code=404, detail="Saved query not found.")
    return restore_query(session, query)
