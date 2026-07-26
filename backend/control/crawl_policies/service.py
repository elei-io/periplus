from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from control.urls import host_matches, normalize_url

from .models import CrawlPolicy
from .schemas import ContentPolicy, CrawlPolicyCreateRequest, CrawlPolicyRecord, CrawlPolicySnapshot

DEFAULT_POLICY_SLUG = "default"
DEFAULT_POLICY_MATCH = ("*", "*", "/", "prefix")


def default_content_policy() -> ContentPolicy:
    """Return the canonical maximum-correctness fallback policy."""

    return ContentPolicy()


def match_for_policy(policy: CrawlPolicy) -> str:
    suffix = "" if policy.path_mode == "exact" else "*"
    return f"{policy.scheme}://{policy.host}{policy.path_prefix}{suffix}"


def _matches(url: str, policy: CrawlPolicy) -> bool:
    parsed = urlparse(normalize_url(url))
    if not policy.enabled or policy.scheme not in {"*", parsed.scheme}:
        return False
    if not host_matches(parsed.hostname or "", policy.host):
        return False
    path = parsed.path or "/"
    return path == policy.path_prefix if policy.path_mode == "exact" else path.startswith(policy.path_prefix)


def _specificity(policy: CrawlPolicy) -> tuple[int, int, int, int, int]:
    host_kind = (
        0 if policy.host == "*" else 1 if policy.host.startswith("*.") else 2
    )
    return (
        int(policy.scheme != "*"),
        host_kind,
        len(policy.host),
        int(policy.path_mode == "exact"),
        len(policy.path_prefix),
    )


def find_crawl_policy_for_url(session: Session, *, url: str) -> CrawlPolicy:
    return find_crawl_policies_for_urls(session, urls=[url])[url]


def find_crawl_policies_for_urls(session: Session, *, urls: list[str]) -> dict[str, CrawlPolicy]:
    policies = list(session.scalars(select(CrawlPolicy).where(CrawlPolicy.enabled.is_(True))))
    result = {}
    for url in urls:
        matches = [policy for policy in policies if _matches(url, policy)]
        if not matches:
            raise RuntimeError("Atlas has no enabled catch-all CrawlPolicy; run deployment setup")
        result[url] = max(matches, key=_specificity)
    return result


def ensure_default_crawl_policy(session: Session) -> CrawlPolicy:
    """Seed the transparent default once, without overriding user ownership."""

    policy = session.scalar(
        select(CrawlPolicy)
        .where(CrawlPolicy.slug == DEFAULT_POLICY_SLUG)
        .with_for_update()
    )
    if policy is not None:
        return policy

    policy = CrawlPolicy(
        slug=DEFAULT_POLICY_SLUG,
        scheme=DEFAULT_POLICY_MATCH[0],
        host=DEFAULT_POLICY_MATCH[1],
        path_prefix=DEFAULT_POLICY_MATCH[2],
        path_mode=DEFAULT_POLICY_MATCH[3],
        content=default_content_policy().model_dump(mode="json"),
        enabled=True,
    )
    session.add(policy)
    session.flush()
    return policy


def policy_snapshot(policy: CrawlPolicy) -> CrawlPolicySnapshot:
    return CrawlPolicySnapshot(id=policy.id, slug=policy.slug, scheme=policy.scheme, host=policy.host, path_prefix=policy.path_prefix, path_mode=policy.path_mode, content=ContentPolicy.model_validate(policy.content or {}))


def _policy_record(policy: CrawlPolicy) -> CrawlPolicyRecord:
    return CrawlPolicyRecord(id=policy.id, slug=policy.slug, scheme=policy.scheme, host=policy.host, path_prefix=policy.path_prefix, path_mode=policy.path_mode, match=match_for_policy(policy), content=ContentPolicy.model_validate(policy.content or {}), enabled=policy.enabled, created_at=policy.created_at, updated_at=policy.updated_at)


def _filtered_policy_statement(*, match_pattern: str | None = None, enabled: bool | None = None) -> Select[tuple[CrawlPolicy]]:
    statement = select(CrawlPolicy)
    if match_pattern:
        needle = match_pattern.strip().replace("*", "")
        statement = statement.where(func.concat(CrawlPolicy.scheme, "://", CrawlPolicy.host, CrawlPolicy.path_prefix).ilike(f"%{needle}%"))
    if enabled is not None:
        statement = statement.where(CrawlPolicy.enabled == enabled)
    return statement


def list_crawl_policies(session: Session, *, match_pattern: str | None = None, enabled: bool | None = None, limit: int = 100, offset: int = 0) -> list[CrawlPolicyRecord]:
    statement = _filtered_policy_statement(match_pattern=match_pattern, enabled=enabled).order_by(CrawlPolicy.updated_at.desc(), CrawlPolicy.created_at.desc()).limit(limit).offset(offset)
    return [_policy_record(policy) for policy in session.scalars(statement)]


def count_crawl_policies(session: Session, **filters) -> int:
    return int(session.scalar(select(func.count()).select_from(_filtered_policy_statement(**filters).subquery())) or 0)


def get_crawl_policy(session: Session, policy_id: UUID) -> CrawlPolicy | None:
    return session.get(CrawlPolicy, policy_id)


def create_crawl_policy(session: Session, request: CrawlPolicyCreateRequest) -> CrawlPolicy:
    values = request.model_dump(mode="json")
    values["content"] = request.content.model_dump(mode="json")
    policy = CrawlPolicy(**values)
    session.add(policy)
    session.flush()
    return policy


def update_crawl_policy(session: Session, *, policy: CrawlPolicy, **changes) -> CrawlPolicy:
    policy = session.scalar(select(CrawlPolicy).where(CrawlPolicy.id == policy.id).with_for_update())
    if policy is None:
        raise ValueError("crawl policy no longer exists")
    if policy.slug == DEFAULT_POLICY_SLUG:
        if changes.get("enabled") is False:
            raise ValueError("the default CrawlPolicy cannot be disabled")
        candidate_match = tuple(
            changes.get(field)
            if changes.get(field) is not None
            else getattr(policy, field)
            for field in ("scheme", "host", "path_prefix", "path_mode")
        )
        if candidate_match != DEFAULT_POLICY_MATCH:
            raise ValueError(
                "the default CrawlPolicy must continue to match every URL"
            )
    for field, value in changes.items():
        if value is not None:
            if field == "content":
                value = ContentPolicy.model_validate(value).model_dump(mode="json")
            setattr(policy, field, value)
    policy.updated_at = datetime.now(UTC)
    session.flush()
    return policy


def delete_crawl_policy(session: Session, *, policy: CrawlPolicy) -> None:
    if policy.slug == DEFAULT_POLICY_SLUG:
        raise ValueError("the default CrawlPolicy cannot be deleted")
    session.delete(policy)
    session.flush()
