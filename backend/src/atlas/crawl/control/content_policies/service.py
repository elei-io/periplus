from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from atlas.urls import host_matches, normalize_url

from .models import ContentPolicy
from .schemas import (
    ContentPolicyConfig,
    ContentPolicyCreateRequest,
    ContentPolicyRecord,
    ContentPolicySnapshot,
)

DEFAULT_POLICY_SLUG = "default"
DEFAULT_POLICY_MATCH = ("*", "*", "/", "prefix")


def default_content_policy() -> ContentPolicyConfig:
    """Return the canonical maximum-correctness fallback policy."""

    return ContentPolicyConfig()


def match_for_policy(policy: ContentPolicy) -> str:
    suffix = "" if policy.path_mode == "exact" else "*"
    return f"{policy.scheme}://{policy.host}{policy.path_prefix}{suffix}"


def _matches(url: str, policy: ContentPolicy) -> bool:
    parsed = urlparse(normalize_url(url))
    if not policy.enabled or policy.scheme not in {"*", parsed.scheme}:
        return False
    if not host_matches(parsed.hostname or "", policy.host):
        return False
    path = parsed.path or "/"
    return path == policy.path_prefix if policy.path_mode == "exact" else path.startswith(policy.path_prefix)


def _specificity(policy: ContentPolicy) -> tuple[int, int, int, int, int]:
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


def find_content_policy_for_url(session: Session, *, url: str) -> ContentPolicy:
    return find_content_policies_for_urls(session, urls=[url])[url]


def find_content_policies_for_urls(session: Session, *, urls: list[str]) -> dict[str, ContentPolicy]:
    policies = list(session.scalars(select(ContentPolicy).where(ContentPolicy.enabled.is_(True))))
    result = {}
    for url in urls:
        matches = [policy for policy in policies if _matches(url, policy)]
        if not matches:
            raise RuntimeError(
                "Atlas has no enabled catch-all content policy; run deployment setup"
            )
        result[url] = max(matches, key=_specificity)
    return result


def ensure_default_content_policy(session: Session) -> ContentPolicy:
    """Seed the transparent default once, without overriding user ownership."""

    policy = session.scalar(
        select(ContentPolicy)
        .where(ContentPolicy.slug == DEFAULT_POLICY_SLUG)
        .with_for_update()
    )
    if policy is not None:
        return policy

    policy = ContentPolicy(
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


def content_policy_snapshot(policy: ContentPolicy) -> ContentPolicySnapshot:
    return ContentPolicySnapshot(id=policy.id, slug=policy.slug, scheme=policy.scheme, host=policy.host, path_prefix=policy.path_prefix, path_mode=policy.path_mode, content=ContentPolicyConfig.model_validate(policy.content or {}))


def _policy_record(policy: ContentPolicy) -> ContentPolicyRecord:
    return ContentPolicyRecord(id=policy.id, slug=policy.slug, scheme=policy.scheme, host=policy.host, path_prefix=policy.path_prefix, path_mode=policy.path_mode, match=match_for_policy(policy), content=ContentPolicyConfig.model_validate(policy.content or {}), enabled=policy.enabled, created_at=policy.created_at, updated_at=policy.updated_at)


def _filtered_policy_statement(*, match_pattern: str | None = None, enabled: bool | None = None) -> Select[tuple[ContentPolicy]]:
    statement = select(ContentPolicy)
    if match_pattern:
        needle = match_pattern.strip().replace("*", "")
        statement = statement.where(func.concat(ContentPolicy.scheme, "://", ContentPolicy.host, ContentPolicy.path_prefix).ilike(f"%{needle}%"))
    if enabled is not None:
        statement = statement.where(ContentPolicy.enabled == enabled)
    return statement


def list_content_policies(session: Session, *, match_pattern: str | None = None, enabled: bool | None = None, limit: int = 100, offset: int = 0) -> list[ContentPolicyRecord]:
    statement = _filtered_policy_statement(match_pattern=match_pattern, enabled=enabled).order_by(ContentPolicy.updated_at.desc(), ContentPolicy.created_at.desc()).limit(limit).offset(offset)
    return [_policy_record(policy) for policy in session.scalars(statement)]


def count_content_policies(session: Session, **filters) -> int:
    return int(session.scalar(select(func.count()).select_from(_filtered_policy_statement(**filters).subquery())) or 0)


def get_content_policy(session: Session, policy_id: UUID) -> ContentPolicy | None:
    return session.get(ContentPolicy, policy_id)


def create_content_policy(session: Session, request: ContentPolicyCreateRequest) -> ContentPolicy:
    values = request.model_dump(mode="json")
    values["content"] = request.content.model_dump(mode="json")
    policy = ContentPolicy(**values)
    session.add(policy)
    session.flush()
    return policy


def update_content_policy(session: Session, *, policy: ContentPolicy, **changes) -> ContentPolicy:
    policy = session.scalar(select(ContentPolicy).where(ContentPolicy.id == policy.id).with_for_update())
    if policy is None:
        raise ValueError("content policy no longer exists")
    if policy.slug == DEFAULT_POLICY_SLUG:
        if changes.get("enabled") is False:
            raise ValueError("the default content policy cannot be disabled")
        candidate_match = tuple(
            changes.get(field)
            if changes.get(field) is not None
            else getattr(policy, field)
            for field in ("scheme", "host", "path_prefix", "path_mode")
        )
        if candidate_match != DEFAULT_POLICY_MATCH:
            raise ValueError(
                "the default content policy must continue to match every URL"
            )
    for field, value in changes.items():
        if value is not None:
            if field == "content":
                value = ContentPolicyConfig.model_validate(value).model_dump(mode="json")
            setattr(policy, field, value)
    policy.updated_at = datetime.now(UTC)
    session.flush()
    return policy


def delete_content_policy(session: Session, *, policy: ContentPolicy) -> None:
    if policy.slug == DEFAULT_POLICY_SLUG:
        raise ValueError("the default content policy cannot be deleted")
    session.delete(policy)
    session.flush()
