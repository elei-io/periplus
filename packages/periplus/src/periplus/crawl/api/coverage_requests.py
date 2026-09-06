from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from periplus.crawl.control.coverage_requests import store
from periplus.crawl.control.coverage_requests.schemas import (
    CoverageRequestCreate, CoverageRequestPage, CoverageRequestRead, CoverageStatus,
)
from periplus.platform.postgres.session import get_session

router = APIRouter(prefix="/coverage-requests", tags=["Coverage requests"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post("", response_model=CoverageRequestRead, status_code=201)
def create(payload: CoverageRequestCreate, session: SessionDependency):
    return store.create_request(session, payload)


@router.get("", response_model=CoverageRequestPage)
def list_requests(
    session: SessionDependency,
    status: CoverageStatus = "pending",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
):
    items, total = store.list_requests(session, status, limit, offset)
    return CoverageRequestPage(items=store.read_requests(session, items), total=total, limit=limit, offset=offset)


@router.get("/{request_id}", response_model=CoverageRequestRead)
def detail(request_id: UUID, session: SessionDependency):
    request = store.get_request(session, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Coverage request not found.")
    return store.read_requests(session, [request])[0]
