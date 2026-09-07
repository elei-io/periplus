from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from periplus.urls import host_matches, normalize_url

from .models import DomainPolicy
from .schemas import DomainPolicyCreateRequest, DomainPolicyRecord, DomainPolicySnapshot

DEFAULT_DOMAIN_POLICY_SLUG = "default-domain"


def _specificity(pattern: str) -> tuple[int, int]:
    return (0 if pattern == "*" else 1 if pattern.startswith("*.") else 2, len(pattern))


def find_domain_policy_for_url(session: Session, *, url: str) -> DomainPolicy:
    return find_domain_policies_for_urls(session, urls=[url])[url]


def find_domain_policies_for_urls(
    session: Session, *, urls: list[str]
) -> dict[str, DomainPolicy]:
    policies = list(session.scalars(select(DomainPolicy).where(DomainPolicy.enabled.is_(True))))
    result = {}
    for url in urls:
        host = (urlparse(normalize_url(url)).hostname or "").lower()
        matches = [
            policy
            for policy in policies
            if host_matches(host, policy.host_match)
        ]
        if not matches:
            raise RuntimeError(
                "Periplus has no enabled catch-all DomainPolicy; run deployment setup"
            )
        result[url] = max(
            matches, key=lambda policy: _specificity(policy.host_match)
        )
    return result


def ensure_default_domain_policy(session: Session) -> DomainPolicy:
    policy = session.scalar(select(DomainPolicy).where(DomainPolicy.slug == DEFAULT_DOMAIN_POLICY_SLUG))
    if policy is None:
        policy = DomainPolicy(slug=DEFAULT_DOMAIN_POLICY_SLUG, host_match="*", maximum_concurrency=4, minimum_request_interval_seconds=0, enabled=True)
        session.add(policy)
        session.flush()
    return policy


def domain_policy_snapshot(policy: DomainPolicy) -> DomainPolicySnapshot:
    return DomainPolicySnapshot.model_validate(policy, from_attributes=True)


def domain_policy_record(policy: DomainPolicy) -> DomainPolicyRecord:
    return DomainPolicyRecord.model_validate(policy, from_attributes=True)


def list_domain_policies(session: Session, *, match_pattern: str | None = None, enabled: bool | None = None, limit: int = 100, offset: int = 0) -> list[DomainPolicyRecord]:
    statement = select(DomainPolicy)
    if match_pattern:
        statement = statement.where(DomainPolicy.host_match.ilike(f"%{match_pattern.strip().replace('*', '')}%"))
    if enabled is not None:
        statement = statement.where(DomainPolicy.enabled == enabled)
    statement = statement.order_by(DomainPolicy.updated_at.desc()).limit(limit).offset(offset)
    return [domain_policy_record(policy) for policy in session.scalars(statement)]


def count_domain_policies(session: Session, *, match_pattern: str | None = None, enabled: bool | None = None) -> int:
    statement = select(DomainPolicy)
    if match_pattern:
        statement = statement.where(DomainPolicy.host_match.ilike(f"%{match_pattern.strip().replace('*', '')}%"))
    if enabled is not None:
        statement = statement.where(DomainPolicy.enabled == enabled)
    return int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)


def get_domain_policy(session: Session, policy_id: UUID) -> DomainPolicy | None:
    return session.get(DomainPolicy, policy_id)


class DomainPolicyVersionConflict(ValueError):
    pass


def _lock_frontier(session: Session) -> None:
    from periplus.crawl.runtime.frontier_models import FrontierControlRecord
    if session.scalar(select(FrontierControlRecord.id).where(FrontierControlRecord.id == 1).with_for_update()) is None:
        raise RuntimeError("frontier control is not installed; run setup")


def create_domain_policy(session: Session, request: DomainPolicyCreateRequest) -> DomainPolicy:
    _lock_frontier(session)
    policy = DomainPolicy(**request.model_dump(), updated_by="admin")
    session.add(policy)
    session.flush()
    return policy


def update_domain_policy(session: Session, *, policy: DomainPolicy, expected_version: int, **changes) -> DomainPolicy:
    _lock_frontier(session)
    current = session.get(DomainPolicy, policy.id, populate_existing=True)
    if current is None or current.version != expected_version:
        raise DomainPolicyVersionConflict("domain policy changed; reload before updating")
    if policy.slug == DEFAULT_DOMAIN_POLICY_SLUG:
        if changes.get("enabled") is False or changes.get("host_match") not in {None, "*"}:
            raise ValueError("the default DomainPolicy must remain enabled and match every host")
    for field, value in changes.items():
        if value is not None:
            setattr(policy, field, value)
    policy.version += 1
    policy.updated_by = "admin"
    policy.updated_at = datetime.now(UTC)
    session.flush()
    return policy


def delete_domain_policy(session: Session, *, policy: DomainPolicy, expected_version: int) -> None:
    _lock_frontier(session)
    current = session.get(DomainPolicy, policy.id, populate_existing=True)
    if current is None or current.version != expected_version:
        raise DomainPolicyVersionConflict("domain policy changed; reload before deleting")
    if policy.slug == DEFAULT_DOMAIN_POLICY_SLUG:
        raise ValueError("the default DomainPolicy cannot be deleted")
    session.delete(policy)
    session.flush()
