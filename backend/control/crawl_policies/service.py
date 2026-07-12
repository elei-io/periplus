from __future__ import annotations

import fnmatch
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from control.crawl_policies.models import CrawlPolicy
from control.crawl_policies.schemas import (
    CrawlPolicyListRecord,
    UrlMatchSnapshot,
)
from control.url_matching import UrlMatch
from control.url_matching import normalize_url


def _match_string(url_match: UrlMatch) -> str:
    return urlunparse((url_match.scheme, url_match.host, url_match.path_pattern, "", "", ""))


def _matches(url: str, url_match: UrlMatch | UrlMatchSnapshot) -> bool:
    parsed = urlparse(normalize_url(url))
    if not getattr(url_match, "enabled", True):
        return False
    if parsed.scheme != url_match.scheme or parsed.netloc != url_match.host:
        return False
    if url_match.match_type == "exact":
        return parsed.path == url_match.path_pattern
    if url_match.match_type == "glob":
        return fnmatch.fnmatch(parsed.path or "/", url_match.path_pattern)
    return False


def _specificity(url_match: UrlMatch | UrlMatchSnapshot) -> tuple[int, int]:
    wildcard_count = url_match.path_pattern.count("*")
    literal_count = len(url_match.path_pattern.replace("*", ""))
    return (url_match.priority, literal_count - wildcard_count)


def find_crawl_policy_for_url(session: Session, *, url: str) -> CrawlPolicy | None:
    policies = session.scalars(
        select(CrawlPolicy)
        .join(UrlMatch, CrawlPolicy.url_match_id == UrlMatch.id)
        .where(CrawlPolicy.enabled.is_(True))
        .where(UrlMatch.enabled.is_(True))
        .order_by(CrawlPolicy.updated_at.desc())
    )
    matches = [policy for policy in policies if policy.url_match is not None and _matches(url, policy.url_match)]
    if not matches:
        return None
    return max(matches, key=lambda policy: _specificity(policy.url_match))


def match_for_policy(policy: CrawlPolicy) -> str:
    if policy.url_match is not None:
        return _match_string(policy.url_match)
    return policy.match


def _sql_like_from_glob(pattern: str) -> str:
    return pattern.replace("%", r"\%").replace("_", r"\_").replace("*", "%")


def _config_value(policy: CrawlPolicy, name: str) -> str | int | None:
    config = policy.config or {}
    value = config.get(name)
    if isinstance(value, str | int):
        return value
    return None


def _config_int(policy: CrawlPolicy, name: str) -> int | None:
    value = _config_value(policy, name)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _list_record(policy: CrawlPolicy) -> CrawlPolicyListRecord:
    return CrawlPolicyListRecord(
        id=policy.id,
        metric_slug=policy.metric_slug,
        domain_group=policy.domain_group,
        url_match_id=policy.url_match_id,
        match=match_for_policy(policy),
        enabled=policy.enabled,
        config=policy.config or {},
        revision=policy.revision,
        template=str(_config_value(policy, "template") or "") or None,
        mode=str(_config_value(policy, "mode") or "") or None,
        wait=str(_config_value(policy, "wait") or "") or None,
        max_concurrency=_config_int(policy, "max_concurrency"),
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def _filtered_statement(
    *,
    match_pattern: str | None = None,
    enabled: bool | None = None,
    template: str | None = None,
    mode: str | None = None,
) -> Select[tuple[CrawlPolicy]]:
    statement = select(CrawlPolicy)
    if match_pattern:
        statement = statement.where(CrawlPolicy.match.ilike(_sql_like_from_glob(match_pattern), escape="\\"))
    if enabled is not None:
        statement = statement.where(CrawlPolicy.enabled == enabled)
    if template:
        statement = statement.where(CrawlPolicy.config["template"].as_string() == template)
    if mode:
        statement = statement.where(CrawlPolicy.config["mode"].as_string() == mode)
    return statement


def list_crawl_policies(
    session: Session,
    *,
    match_pattern: str | None = None,
    enabled: bool | None = None,
    template: str | None = None,
    mode: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[CrawlPolicyListRecord]:
    statement = (
        _filtered_statement(
            match_pattern=match_pattern,
            enabled=enabled,
            template=template,
            mode=mode,
        )
        .order_by(CrawlPolicy.updated_at.desc(), CrawlPolicy.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [_list_record(policy) for policy in session.scalars(statement)]


def count_crawl_policies(
    session: Session,
    *,
    match_pattern: str | None = None,
    enabled: bool | None = None,
    template: str | None = None,
    mode: str | None = None,
) -> int:
    statement = select(func.count()).select_from(
        _filtered_statement(
            match_pattern=match_pattern,
            enabled=enabled,
            template=template,
            mode=mode,
        ).subquery()
    )
    return int(session.scalar(statement) or 0)


def get_crawl_policy(session: Session, policy_id: UUID) -> CrawlPolicy | None:
    return session.get(CrawlPolicy, policy_id)


def update_crawl_policy(
    session: Session,
    *,
    policy: CrawlPolicy,
    enabled: bool | None = None,
    match: str | None = None,
    config: dict | None = None,
    domain_group: str | None = None,
) -> CrawlPolicy:
    policy = session.scalar(
        select(CrawlPolicy)
        .where(CrawlPolicy.id == policy.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if policy is None:
        raise ValueError("crawl policy no longer exists")
    policy.revision += 1
    if enabled is not None:
        policy.enabled = enabled
    if match is not None:
        policy.match = match
    if config is not None:
        policy.config = config
    if domain_group is not None:
        normalized_group = domain_group.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", normalized_group):
            raise ValueError("domain_group must contain 1-63 lowercase letters, numbers, underscores, or hyphens")
        policy.domain_group = normalized_group
    policy.updated_at = datetime.now(UTC)
    session.flush()
    return policy


def delete_crawl_policy(session: Session, *, policy: CrawlPolicy) -> None:
    session.delete(policy)
    session.flush()
