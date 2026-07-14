from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, joinedload

from control.urls import normalize_url

from .models import CrawlPolicy, CrawlProfile
from .schemas import (
    CrawlPolicyCreateRequest,
    CrawlPolicyRecord,
    CrawlProfileCreateRequest,
    CrawlProfileSnapshot,
    parse_profile_config,
)

DEFAULT_POLICY_SLUG = "default"


def profile_config_hash(config: dict) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


SEEDED_PROFILES: tuple[dict, ...] = (
    {
        "slug": "direct",
        "name": "Direct",
        "description": "Plain HTTP without a browser.",
        "transport": "http",
        "config": {},
        "cost_rank": 10,
        "trial_eligible": True,
    },
    {
        "slug": "rendered",
        "name": "Rendered",
        "description": "Lightweight browser acquisition through initial DOM readiness.",
        "transport": "browser",
        "config": {"mode": "static", "wait": "none", "run_config_overrides": {}},
        "cost_rank": 20,
        "trial_eligible": True,
    },
    {
        "slug": "settled",
        "name": "Settled",
        "description": "Browser acquisition that waits for DOM and link stability.",
        "transport": "browser",
        "config": {"mode": "static", "wait": "stable", "run_config_overrides": {}},
        "cost_rank": 30,
        "trial_eligible": True,
    },
    {
        "slug": "full-page",
        "name": "Full page",
        "description": "Dynamic browser acquisition after the page has settled.",
        "transport": "browser",
        "config": {"mode": "dynamic", "wait": "stable", "run_config_overrides": {}},
        "cost_rank": 40,
        "trial_eligible": True,
    },
    {
        "slug": "interactive",
        "name": "Interactive",
        "description": "Longer application hydration and deeper scrolling.",
        "transport": "browser",
        "config": {
            "mode": "app",
            "wait": "stable",
            "run_config_overrides": {
                "delay_before_return_html": 6.0,
                "max_scroll_steps": 8,
                "scroll_delay": 1.0,
            },
        },
        "cost_rank": 50,
        "trial_eligible": True,
    },
)


def profile_snapshot(profile: CrawlProfile) -> CrawlProfileSnapshot:
    return CrawlProfileSnapshot(
        id=profile.id,
        slug=profile.slug,
        name=profile.name,
        transport=profile.transport,
        config=profile.config or {},
        cost_rank=profile.cost_rank,
    )


def match_for_policy(policy: CrawlPolicy) -> str:
    suffix = "" if policy.path_mode == "exact" else "*"
    return f"{policy.scheme}://{policy.host}{policy.path_prefix}{suffix}"


def _matches(url: str, policy: CrawlPolicy) -> bool:
    parsed = urlparse(normalize_url(url))
    if not policy.enabled:
        return False
    if policy.scheme not in {"*", parsed.scheme}:
        return False
    if policy.host not in {"*", parsed.netloc}:
        return False
    path = parsed.path or "/"
    if policy.path_mode == "exact":
        return path == policy.path_prefix
    return path.startswith(policy.path_prefix)


def _specificity(policy: CrawlPolicy) -> tuple[int, int, int, int]:
    return (
        int(policy.scheme != "*"),
        int(policy.host != "*"),
        int(policy.path_mode == "exact"),
        len(policy.path_prefix),
    )


def find_crawl_policy_for_url(session: Session, *, url: str) -> CrawlPolicy:
    return find_crawl_policies_for_urls(session, urls=[url])[url]


def find_crawl_policies_for_urls(
    session: Session, *, urls: list[str]
) -> dict[str, CrawlPolicy]:
    policies = list(
        session.scalars(
            select(CrawlPolicy)
            .options(joinedload(CrawlPolicy.profile))
            .where(CrawlPolicy.enabled.is_(True))
        ).unique()
    )
    result: dict[str, CrawlPolicy] = {}
    for url in urls:
        matches = [policy for policy in policies if _matches(url, policy)]
        if not matches:
            raise RuntimeError(
                "Atlas has no enabled catch-all CrawlPolicy; run deployment setup"
            )
        result[url] = max(matches, key=_specificity)
    return result


def ensure_crawl_profiles(session: Session) -> dict[str, CrawlProfile]:
    existing = {
        profile.slug: profile
        for profile in session.scalars(select(CrawlProfile))
    }
    for values in SEEDED_PROFILES:
        if values["slug"] in existing:
            continue
        profile = CrawlProfile(**values)
        parse_profile_config(profile.transport, profile.config)
        session.add(profile)
        existing[profile.slug] = profile
    session.flush()
    return existing


def ensure_default_crawl_policy(session: Session) -> CrawlPolicy:
    """Ensure every URL has one explicit policy resolution path."""

    profiles = ensure_crawl_profiles(session)
    policy = session.scalar(
        select(CrawlPolicy)
        .options(joinedload(CrawlPolicy.profile))
        .where(CrawlPolicy.slug == DEFAULT_POLICY_SLUG)
    )
    if policy is None:
        policy = CrawlPolicy(
            slug=DEFAULT_POLICY_SLUG,
            scheme="*",
            host="*",
            path_prefix="/",
            path_mode="prefix",
            profile_id=profiles["direct"].id,
            max_concurrency=4,
            enabled=True,
        )
        policy.profile = profiles["direct"]
        session.add(policy)
        session.flush()
    return policy


def next_trial_profile(session: Session, profile: CrawlProfile) -> CrawlProfile | None:
    return session.scalar(
        select(CrawlProfile)
        .where(CrawlProfile.trial_eligible.is_(True))
        .where(CrawlProfile.cost_rank > profile.cost_rank)
        .order_by(CrawlProfile.cost_rank.asc())
        .limit(1)
    )


def _policy_record(policy: CrawlPolicy) -> CrawlPolicyRecord:
    return CrawlPolicyRecord(
        id=policy.id,
        slug=policy.slug,
        scheme=policy.scheme,
        host=policy.host,
        path_prefix=policy.path_prefix,
        path_mode=policy.path_mode,
        match=match_for_policy(policy),
        profile=policy.profile,
        max_concurrency=policy.max_concurrency,
        enabled=policy.enabled,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def _filtered_policy_statement(
    *,
    match_pattern: str | None = None,
    enabled: bool | None = None,
    profile_slug: str | None = None,
    transport: str | None = None,
) -> Select[tuple[CrawlPolicy]]:
    statement = select(CrawlPolicy).join(CrawlProfile)
    if match_pattern:
        needle = match_pattern.strip().replace("*", "")
        statement = statement.where(
            func.concat(
                CrawlPolicy.scheme,
                "://",
                CrawlPolicy.host,
                CrawlPolicy.path_prefix,
            ).ilike(f"%{needle}%")
        )
    if enabled is not None:
        statement = statement.where(CrawlPolicy.enabled == enabled)
    if profile_slug:
        statement = statement.where(CrawlProfile.slug == profile_slug)
    if transport:
        statement = statement.where(CrawlProfile.transport == transport)
    return statement


def list_crawl_policies(
    session: Session,
    *,
    match_pattern: str | None = None,
    enabled: bool | None = None,
    profile_slug: str | None = None,
    transport: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[CrawlPolicyRecord]:
    statement = (
        _filtered_policy_statement(
            match_pattern=match_pattern,
            enabled=enabled,
            profile_slug=profile_slug,
            transport=transport,
        )
        .options(joinedload(CrawlPolicy.profile))
        .order_by(CrawlPolicy.updated_at.desc(), CrawlPolicy.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [_policy_record(policy) for policy in session.scalars(statement).unique()]


def count_crawl_policies(session: Session, **filters) -> int:
    statement = select(func.count()).select_from(
        _filtered_policy_statement(**filters).subquery()
    )
    return int(session.scalar(statement) or 0)


def get_crawl_policy(session: Session, policy_id: UUID) -> CrawlPolicy | None:
    return session.scalar(
        select(CrawlPolicy)
        .options(joinedload(CrawlPolicy.profile))
        .where(CrawlPolicy.id == policy_id)
    )


def create_crawl_policy(session: Session, request: CrawlPolicyCreateRequest) -> CrawlPolicy:
    profile = session.get(CrawlProfile, request.profile_id)
    if profile is None:
        raise ValueError("crawl profile does not exist")
    policy = CrawlPolicy(**request.model_dump())
    policy.profile = profile
    session.add(policy)
    session.flush()
    return policy


def update_crawl_policy(
    session: Session,
    *,
    policy: CrawlPolicy,
    enabled: bool | None = None,
    scheme: str | None = None,
    host: str | None = None,
    path_prefix: str | None = None,
    path_mode: str | None = None,
    profile_id: UUID | None = None,
    max_concurrency: int | None = None,
) -> CrawlPolicy:
    policy = session.scalar(
        select(CrawlPolicy)
        .options(joinedload(CrawlPolicy.profile))
        .where(CrawlPolicy.id == policy.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if policy is None:
        raise ValueError("crawl policy no longer exists")
    if policy.slug == DEFAULT_POLICY_SLUG:
        if enabled is False:
            raise ValueError("the default CrawlPolicy cannot be disabled")
        candidate = (
            scheme if scheme is not None else policy.scheme,
            host if host is not None else policy.host,
            path_prefix if path_prefix is not None else policy.path_prefix,
            path_mode if path_mode is not None else policy.path_mode,
        )
        if candidate != ("*", "*", "/", "prefix"):
            raise ValueError("the default CrawlPolicy must continue to match every URL")
    if profile_id is not None:
        profile = session.get(CrawlProfile, profile_id)
        if profile is None:
            raise ValueError("crawl profile does not exist")
        policy.profile_id = profile.id
        policy.profile = profile
    for field, value in (
        ("enabled", enabled),
        ("scheme", scheme),
        ("host", host),
        ("path_prefix", path_prefix),
        ("path_mode", path_mode),
        ("max_concurrency", max_concurrency),
    ):
        if value is not None:
            setattr(policy, field, value)
    policy.updated_at = datetime.now(UTC)
    session.flush()
    return policy


def delete_crawl_policy(session: Session, *, policy: CrawlPolicy) -> None:
    if policy.slug == DEFAULT_POLICY_SLUG:
        raise ValueError("the default CrawlPolicy cannot be deleted")
    session.delete(policy)
    session.flush()


def list_crawl_profiles(
    session: Session, *, limit: int = 100, offset: int = 0
) -> list[CrawlProfile]:
    return list(
        session.scalars(
            select(CrawlProfile)
            .order_by(CrawlProfile.cost_rank, CrawlProfile.slug)
            .limit(limit)
            .offset(offset)
        )
    )


def count_crawl_profiles(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(CrawlProfile)) or 0)


def get_crawl_profile(session: Session, profile_id: UUID) -> CrawlProfile | None:
    return session.get(CrawlProfile, profile_id)


def get_crawl_profile_by_slug(session: Session, slug: str) -> CrawlProfile | None:
    return session.scalar(select(CrawlProfile).where(CrawlProfile.slug == slug))


def create_crawl_profile(session: Session, request: CrawlProfileCreateRequest) -> CrawlProfile:
    parse_profile_config(request.transport, request.config)
    profile = CrawlProfile(**request.model_dump())
    session.add(profile)
    session.flush()
    return profile


def update_crawl_profile(
    session: Session,
    *,
    profile: CrawlProfile,
    name: str | None = None,
    description: str | None = None,
    description_set: bool = False,
    config: dict | None = None,
    cost_rank: int | None = None,
    trial_eligible: bool | None = None,
) -> CrawlProfile:
    profile = session.scalar(
        select(CrawlProfile).where(CrawlProfile.id == profile.id).with_for_update()
    )
    if profile is None:
        raise ValueError("crawl profile no longer exists")
    if config is not None:
        parse_profile_config(profile.transport, config)
        profile.config = config
    for field, value in (
        ("name", name),
        ("cost_rank", cost_rank),
        ("trial_eligible", trial_eligible),
    ):
        if value is not None:
            setattr(profile, field, value)
    if description_set:
        profile.description = description
    profile.updated_at = datetime.now(UTC)
    session.flush()
    return profile


def apply_policy_trial_candidate(
    session: Session,
    *,
    scheme: str,
    host: str,
    port: int,
    profile_slug: str,
) -> CrawlPolicy:
    """Apply one sampled profile as the broad policy for an observed origin."""

    profile = get_crawl_profile_by_slug(session, profile_slug)
    if profile is None:
        raise ValueError(f"Unknown crawl profile: {profile_slug}")
    if scheme not in {"http", "https"}:
        raise ValueError("Policy trial scheme must be HTTP or HTTPS")
    normalized_host = host.strip().lower()
    if not normalized_host or "/" in normalized_host:
        raise ValueError("Policy trial host is invalid")
    default_port = {"http": 80, "https": 443}[scheme]
    match_host = normalized_host if port == default_port else f"{normalized_host}:{port}"
    policy = session.scalar(
        select(CrawlPolicy)
        .options(joinedload(CrawlPolicy.profile))
        .where(
            CrawlPolicy.scheme == scheme,
            CrawlPolicy.host == match_host,
            CrawlPolicy.path_prefix == "/",
            CrawlPolicy.path_mode == "prefix",
        )
        .with_for_update()
    )
    if policy is None:
        slug_host = "".join(
            char if char.isalnum() else "-" for char in match_host.lower()
        ).strip("-")
        policy = CrawlPolicy(
            slug=f"{slug_host[:48]}-{profile.slug}",
            scheme=scheme,
            host=match_host,
            path_prefix="/",
            path_mode="prefix",
            profile_id=profile.id,
            max_concurrency=4,
            enabled=True,
        )
        policy.profile = profile
        session.add(policy)
    else:
        policy.profile_id = profile.id
        policy.profile = profile
        policy.enabled = True
        policy.updated_at = datetime.now(UTC)
    session.flush()
    return policy
