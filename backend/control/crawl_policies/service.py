from __future__ import annotations

import fnmatch
import re
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, joinedload

from control.crawl_policies.models import CrawlPolicy
from control.crawl_policies.schemas import (
    CrawlPolicyConfig,
    CrawlPolicyListRecord,
    UrlMatchSnapshot,
)
from control.crawl_policies.templates import get_crawl_policy_template
from control.url_matching import UrlMatch
from control.url_matching import normalize_url

DEFAULT_POLICY_METRIC_SLUG = "system-default"
DEFAULT_POLICY_DOMAIN_GROUP = "public-web"
DEFAULT_POLICY_MATCH = "*://*/*"


def _match_string(url_match: UrlMatch) -> str:
    return urlunparse((url_match.scheme, url_match.host, url_match.path_pattern, "", "", ""))


def _matches(url: str, url_match: UrlMatch | UrlMatchSnapshot) -> bool:
    parsed = urlparse(normalize_url(url))
    if not getattr(url_match, "enabled", True):
        return False
    if url_match.scheme not in {"*", parsed.scheme}:
        return False
    if url_match.host not in {"*", parsed.netloc}:
        return False
    if url_match.match_type == "exact":
        return parsed.path == url_match.path_pattern
    if url_match.match_type == "glob":
        return fnmatch.fnmatch(parsed.path or "/", url_match.path_pattern)
    return False


def _specificity(
    url_match: UrlMatch | UrlMatchSnapshot,
) -> tuple[int, int, int, int]:
    wildcard_count = url_match.path_pattern.count("*")
    literal_count = len(url_match.path_pattern.replace("*", ""))
    return (
        url_match.priority,
        int(url_match.scheme != "*"),
        int(url_match.host != "*"),
        literal_count - wildcard_count,
    )


def find_crawl_policy_for_url(session: Session, *, url: str) -> CrawlPolicy:
    return find_crawl_policies_for_urls(session, urls=[url])[url]


def find_crawl_policies_for_urls(
    session: Session, *, urls: list[str]
) -> dict[str, CrawlPolicy]:
    policies = list(session.scalars(
        select(CrawlPolicy)
        .join(UrlMatch, CrawlPolicy.url_match_id == UrlMatch.id)
        .options(joinedload(CrawlPolicy.url_match))
        .where(CrawlPolicy.enabled.is_(True))
        .where(UrlMatch.enabled.is_(True))
        .order_by(CrawlPolicy.updated_at.desc())
    ))
    result: dict[str, CrawlPolicy] = {}
    for url in urls:
        matches = [
            policy
            for policy in policies
            if policy.url_match is not None and _matches(url, policy.url_match)
        ]
        if not matches:
            raise RuntimeError(
                "Atlas has no enabled catch-all CrawlPolicy; run deployment setup"
            )
        result[url] = max(
            matches, key=lambda policy: _specificity(policy.url_match)
        )
    return result


def ensure_default_crawl_policy(session: Session) -> CrawlPolicy:
    """Ensure every URL has one explicit, editable policy resolution path."""

    url_match = session.scalar(
        select(UrlMatch).where(
            UrlMatch.scheme == "*",
            UrlMatch.host == "*",
            UrlMatch.path_pattern == "/*",
            UrlMatch.match_type == "glob",
            UrlMatch.query_policy == "ignore",
        )
    )
    if url_match is None:
        url_match = UrlMatch(
            scheme="*",
            host="*",
            domain="*",
            path_pattern="/*",
            match_type="glob",
            query_policy="ignore",
            enabled=True,
            priority=-1_000_000,
        )
        session.add(url_match)
        session.flush()

    policy = session.scalar(
        select(CrawlPolicy)
        .where(CrawlPolicy.url_match_id == url_match.id)
        .order_by(CrawlPolicy.created_at.asc())
        .limit(1)
    )
    if policy is None:
        policy = CrawlPolicy(
            metric_slug=DEFAULT_POLICY_METRIC_SLUG,
            domain_group=DEFAULT_POLICY_DOMAIN_GROUP,
            url_match_id=url_match.id,
            match=DEFAULT_POLICY_MATCH,
            enabled=True,
            config=CrawlPolicyConfig(
                profile="http",
                concurrency=4,
                config={"template": "http_fast"},
            ).model_dump(mode="json"),
        )
        policy.url_match = url_match
        session.add(policy)
        session.flush()
    return policy


def match_for_policy(policy: CrawlPolicy) -> str:
    if policy.url_match is not None:
        return _match_string(policy.url_match)
    return policy.match


def _sql_like_from_glob(pattern: str) -> str:
    return pattern.replace("%", r"\%").replace("_", r"\_").replace("*", "%")


def _list_record(policy: CrawlPolicy) -> CrawlPolicyListRecord:
    envelope = CrawlPolicyConfig.model_validate(policy.config or {})
    profile_config = envelope.parsed_config()
    return CrawlPolicyListRecord(
        id=policy.id,
        metric_slug=policy.metric_slug,
        domain_group=policy.domain_group,
        url_match_id=policy.url_match_id,
        match=match_for_policy(policy),
        enabled=policy.enabled,
        config=policy.config or {},
        revision=policy.revision,
        template=str(getattr(profile_config, "template", "") or "") or None,
        profile=envelope.profile,
        mode=str(getattr(profile_config, "mode", "") or "") or None,
        wait=str(getattr(profile_config, "wait", "") or "") or None,
        concurrency=envelope.concurrency,
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
        statement = statement.where(CrawlPolicy.config["config"]["template"].as_string() == template)
    if mode:
        statement = statement.where(CrawlPolicy.config["config"]["mode"].as_string() == mode)
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
    if policy.metric_slug == DEFAULT_POLICY_METRIC_SLUG:
        if enabled is False:
            raise ValueError("the default CrawlPolicy cannot be disabled")
        if match is not None and match != DEFAULT_POLICY_MATCH:
            raise ValueError("the default CrawlPolicy must continue to match every URL")
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
    if policy.metric_slug == DEFAULT_POLICY_METRIC_SLUG:
        raise ValueError("the default CrawlPolicy cannot be deleted")
    session.delete(policy)
    session.flush()


def apply_policy_trial_candidate(
    session: Session,
    *,
    scheme: str,
    host: str,
    port: int,
    registrable_domain: str,
    template_name: str,
) -> CrawlPolicy:
    """Apply one sampled template as the broad policy for an observed origin."""

    template = get_crawl_policy_template(template_name)
    if template is None:
        raise ValueError(f"Unknown crawl policy template: {template_name}")
    if scheme not in {"http", "https"}:
        raise ValueError("Policy trial scheme must be HTTP or HTTPS")
    normalized_host = host.strip().lower()
    if not normalized_host or "/" in normalized_host:
        raise ValueError("Policy trial host is invalid")
    default_port = {"http": 80, "https": 443}[scheme]
    match_host = normalized_host if port == default_port else f"{normalized_host}:{port}"
    match_string = f"{scheme}://{match_host}/*"

    url_match = session.scalar(
        select(UrlMatch)
        .where(
            UrlMatch.scheme == scheme,
            UrlMatch.host == match_host,
            UrlMatch.path_pattern == "/*",
            UrlMatch.match_type == "glob",
            UrlMatch.query_policy == "ignore",
        )
        .with_for_update()
    )
    if url_match is None:
        url_match = UrlMatch(
            scheme=scheme,
            host=match_host,
            domain=registrable_domain,
            path_pattern="/*",
            match_type="glob",
            query_policy="ignore",
            enabled=True,
            priority=0,
        )
        session.add(url_match)
        session.flush()
    elif not url_match.enabled:
        url_match.enabled = True
        url_match.updated_at = datetime.now(UTC)

    policy = session.scalar(
        select(CrawlPolicy)
        .where(CrawlPolicy.url_match_id == url_match.id)
        .order_by(CrawlPolicy.updated_at.desc())
        .limit(1)
        .with_for_update()
    )
    config = template.policy_config()
    if policy is None:
        domain_group = re.sub(r"[^a-z0-9_-]+", "-", registrable_domain.lower()).strip("-")
        if not domain_group:
            raise ValueError("Policy trial domain does not produce a valid domain group")
        policy = CrawlPolicy(
            domain_group=domain_group[:63],
            url_match_id=url_match.id,
            match=match_string,
            enabled=True,
            config=config,
        )
        policy.url_match = url_match
        session.add(policy)
    elif not policy.enabled or policy.config != config or policy.match != match_string:
        policy.enabled = True
        policy.config = config
        policy.match = match_string
        policy.revision += 1
        policy.updated_at = datetime.now(UTC)
    session.flush()
    return policy
