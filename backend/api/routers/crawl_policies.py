from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from control.crawl_policies.schemas import (
    CrawlPolicyCreateRequest,
    CrawlPolicyListResponse,
    CrawlPolicyRecord,
    CrawlPolicyUpdateRequest,
)
from control.crawl_policies.service import (
    count_crawl_policies,
    create_crawl_policy,
    delete_crawl_policy,
    get_crawl_policy,
    list_crawl_policies,
    match_for_policy,
    update_crawl_policy,
)
from db.session import get_session

router = APIRouter(prefix="/crawl-policies", tags=["crawl-policies"])


def _record(policy) -> CrawlPolicyRecord:
    return CrawlPolicyRecord(
        id=policy.id,
        slug=policy.slug,
        scheme=policy.scheme,
        host=policy.host,
        path_prefix=policy.path_prefix,
        path_mode=policy.path_mode,
        match=match_for_policy(policy),
        content=policy.content or {},
        enabled=policy.enabled,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


@router.get("/", response_model=CrawlPolicyListResponse)
def list_(
    session: Annotated[Session, Depends(get_session)],
    match_pattern: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CrawlPolicyListResponse:
    filters = {"match_pattern": match_pattern, "enabled": enabled}
    return CrawlPolicyListResponse(
        items=list_crawl_policies(session, **filters, limit=limit, offset=offset),
        total=count_crawl_policies(session, **filters),
        limit=limit,
        offset=offset,
    )


@router.post("/", response_model=CrawlPolicyRecord, status_code=201)
def create(payload: CrawlPolicyCreateRequest, session: Annotated[Session, Depends(get_session)]) -> CrawlPolicyRecord:
    try:
        return _record(create_crawl_policy(session, payload))
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{policy_id}", response_model=CrawlPolicyRecord)
def get(policy_id: UUID, session: Annotated[Session, Depends(get_session)]) -> CrawlPolicyRecord:
    policy = get_crawl_policy(session, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")
    return _record(policy)


@router.patch("/{policy_id}", response_model=CrawlPolicyRecord)
def update(policy_id: UUID, payload: CrawlPolicyUpdateRequest, session: Annotated[Session, Depends(get_session)]) -> CrawlPolicyRecord:
    policy = get_crawl_policy(session, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")
    try:
        return _record(update_crawl_policy(session, policy=policy, **payload.model_dump(exclude_unset=True)))
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{policy_id}", status_code=204)
def delete(policy_id: UUID, session: Annotated[Session, Depends(get_session)]) -> None:
    policy = get_crawl_policy(session, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")
    try:
        delete_crawl_policy(session, policy=policy)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
