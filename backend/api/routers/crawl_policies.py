from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from crawl_policies.schemas import (
    CrawlPolicyListResponse,
    CrawlPolicyRecord,
    CrawlPolicyUpdateRequest,
)
from crawl_policies.service import (
    count_crawl_policies,
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
        metric_slug=policy.metric_slug,
        domain_group=policy.domain_group,
        url_match_id=policy.url_match_id,
        match=match_for_policy(policy),
        enabled=policy.enabled,
        config=policy.config or {},
        revision=policy.revision,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


@router.get("/", response_model=CrawlPolicyListResponse)
def list_(
    session: Annotated[Session, Depends(get_session)],
    match_pattern: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    template: Annotated[str | None, Query()] = None,
    mode: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CrawlPolicyListResponse:
    return CrawlPolicyListResponse(
        items=list_crawl_policies(
            session=session,
            match_pattern=match_pattern,
            enabled=enabled,
            template=template,
            mode=mode,
            limit=limit,
            offset=offset,
        ),
        total=count_crawl_policies(
            session=session,
            match_pattern=match_pattern,
            enabled=enabled,
            template=template,
            mode=mode,
        ),
        limit=limit,
        offset=offset,
    )


@router.get("/{policy_id}", response_model=CrawlPolicyRecord)
def get(
    policy_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlPolicyRecord:
    policy = get_crawl_policy(session=session, policy_id=policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")

    return _record(policy)


@router.patch("/{policy_id}", response_model=CrawlPolicyRecord)
def update(
    policy_id: UUID,
    request: CrawlPolicyUpdateRequest,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlPolicyRecord:
    policy = get_crawl_policy(session=session, policy_id=policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")

    updated = update_crawl_policy(
        session=session,
        policy=policy,
        enabled=request.enabled,
        match=request.match,
        config=request.config,
        domain_group=request.domain_group,
    )
    return _record(updated)


@router.delete("/{policy_id}", status_code=204)
def delete(
    policy_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    policy = get_crawl_policy(session=session, policy_id=policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")

    delete_crawl_policy(session=session, policy=policy)
