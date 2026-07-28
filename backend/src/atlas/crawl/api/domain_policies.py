from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from atlas.crawl.control.domain_policies.schemas import (
    DomainPolicyCreateRequest,
    DomainPolicyListResponse,
    DomainPolicyRecord,
    DomainPolicyUpdateRequest,
)
from atlas.crawl.control.domain_policies.service import (
    count_domain_policies,
    create_domain_policy,
    delete_domain_policy,
    domain_policy_record,
    get_domain_policy,
    list_domain_policies,
    update_domain_policy,
)
from atlas.platform.postgres.session import get_session

router = APIRouter(prefix="/domain-policies", tags=["domain-policies"])


@router.get("/", response_model=DomainPolicyListResponse)
def list_(session: Annotated[Session, Depends(get_session)], match_pattern: Annotated[str | None, Query()] = None, enabled: Annotated[bool | None, Query()] = None, limit: Annotated[int, Query(ge=1, le=500)] = 100, offset: Annotated[int, Query(ge=0)] = 0) -> DomainPolicyListResponse:
    filters = {"match_pattern": match_pattern, "enabled": enabled}
    return DomainPolicyListResponse(items=list_domain_policies(session, **filters, limit=limit, offset=offset), total=count_domain_policies(session, **filters), limit=limit, offset=offset)


@router.post("/", response_model=DomainPolicyRecord, status_code=201)
def create(payload: DomainPolicyCreateRequest, session: Annotated[Session, Depends(get_session)]) -> DomainPolicyRecord:
    try:
        return domain_policy_record(create_domain_policy(session, payload))
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{policy_id}", response_model=DomainPolicyRecord)
def get(policy_id: UUID, session: Annotated[Session, Depends(get_session)]) -> DomainPolicyRecord:
    policy = get_domain_policy(session, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Domain policy not found.")
    return domain_policy_record(policy)


@router.patch("/{policy_id}", response_model=DomainPolicyRecord)
def update(policy_id: UUID, payload: DomainPolicyUpdateRequest, session: Annotated[Session, Depends(get_session)]) -> DomainPolicyRecord:
    policy = get_domain_policy(session, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Domain policy not found.")
    try:
        return domain_policy_record(update_domain_policy(session, policy=policy, **payload.model_dump(exclude_unset=True)))
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{policy_id}", status_code=204)
def delete(policy_id: UUID, session: Annotated[Session, Depends(get_session)]) -> None:
    policy = get_domain_policy(session, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Domain policy not found.")
    try:
        delete_domain_policy(session, policy=policy)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
