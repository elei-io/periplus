from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from atlas.crawl.control.content_policies.schemas import (
    ContentPolicyCreateRequest,
    ContentPolicyListResponse,
    ContentPolicyRecord,
    ContentPolicyUpdateRequest,
)
from atlas.crawl.control.content_policies.service import (
    count_content_policies,
    create_content_policy,
    delete_content_policy,
    get_content_policy,
    list_content_policies,
    match_for_policy,
    update_content_policy,
)
from atlas.platform.postgres.session import get_session

router = APIRouter(prefix="/content-policies", tags=["content-policies"])


def _record(policy) -> ContentPolicyRecord:
    return ContentPolicyRecord(
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


@router.get("/", response_model=ContentPolicyListResponse)
def list_(
    session: Annotated[Session, Depends(get_session)],
    match_pattern: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ContentPolicyListResponse:
    filters = {"match_pattern": match_pattern, "enabled": enabled}
    return ContentPolicyListResponse(
        items=list_content_policies(session, **filters, limit=limit, offset=offset),
        total=count_content_policies(session, **filters),
        limit=limit,
        offset=offset,
    )


@router.post("/", response_model=ContentPolicyRecord, status_code=201)
def create(payload: ContentPolicyCreateRequest, session: Annotated[Session, Depends(get_session)]) -> ContentPolicyRecord:
    try:
        return _record(create_content_policy(session, payload))
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{policy_id}", response_model=ContentPolicyRecord)
def get(policy_id: UUID, session: Annotated[Session, Depends(get_session)]) -> ContentPolicyRecord:
    policy = get_content_policy(session, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Content policy not found.")
    return _record(policy)


@router.patch("/{policy_id}", response_model=ContentPolicyRecord)
def update(policy_id: UUID, payload: ContentPolicyUpdateRequest, session: Annotated[Session, Depends(get_session)]) -> ContentPolicyRecord:
    policy = get_content_policy(session, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Content policy not found.")
    try:
        return _record(update_content_policy(session, policy=policy, **payload.model_dump(exclude_unset=True)))
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{policy_id}", status_code=204)
def delete(policy_id: UUID, session: Annotated[Session, Depends(get_session)]) -> None:
    policy = get_content_policy(session, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Content policy not found.")
    try:
        delete_content_policy(session, policy=policy)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
