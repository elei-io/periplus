from __future__ import annotations

from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urldefrag, urlparse, urlunparse
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artifacts.models import Artifact
from artifacts.service import BYTE_ARTIFACT_KINDS, is_cache_eligible, warning_count
from crawls.models import Crawl

from .models import Url, UrlMatch
from .schemas import UrlListRecord


def normalize_url(value: str) -> str:
    url, _ = urldefrag(value.strip())
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def query_fingerprint(value: str) -> str | None:
    parsed = urlparse(value)
    if not parsed.query:
        return None

    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    return sha256(query.encode()).hexdigest()


def resolve_url(session: Session, value: str) -> Url:
    normalized = normalize_url(value)
    existing = session.scalar(select(Url).where(Url.normalized_url == normalized))
    if existing is not None:
        return existing

    parsed = urlparse(normalized)
    url = Url(
        url=value,
        normalized_url=normalized,
        scheme=parsed.scheme,
        host=parsed.netloc,
        domain=parsed.netloc.removeprefix("www."),
        path=parsed.path or "/",
        query=parsed.query or None,
        query_fingerprint=query_fingerprint(normalized),
    )
    session.add(url)
    session.flush()
    return url


def resolve_url_match_for_url(
    session: Session,
    value: str,
    *,
    task_run_id: UUID | None = None,
) -> UrlMatch:
    normalized = normalize_url(value)
    parsed = urlparse(normalized)
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    path_pattern = parsed.path or "/"
    match_type = "exact"
    query_policy = "ignore"
    existing = session.scalar(
        select(UrlMatch).where(
            UrlMatch.scheme == scheme,
            UrlMatch.host == host,
            UrlMatch.path_pattern == path_pattern,
            UrlMatch.match_type == match_type,
            UrlMatch.query_policy == query_policy,
        )
    )
    if existing is not None:
        existing.updated_by_task_run_id = task_run_id
        session.flush()
        return existing

    url_match = UrlMatch(
        scheme=scheme,
        host=host,
        domain=host.removeprefix("www."),
        path_pattern=path_pattern,
        match_type=match_type,
        query_policy=query_policy,
        created_by_task_run_id=task_run_id,
        updated_by_task_run_id=task_run_id,
    )
    session.add(url_match)
    session.flush()
    return url_match


def _sql_like_from_glob(pattern: str) -> str:
    return pattern.replace("%", r"\%").replace("_", r"\_").replace("*", "%")


def list_urls(
    session: Session,
    *,
    url_pattern: str | None = None,
    domain: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[UrlListRecord]:
    statement = select(Url)
    if url_pattern:
        statement = statement.where(Url.normalized_url.ilike(_sql_like_from_glob(url_pattern), escape="\\"))
    if domain:
        statement = statement.where(Url.domain.ilike(f"%{domain}%"))

    statement = statement.order_by(Url.normalized_url.asc()).limit(limit).offset(offset)
    rows: list[UrlListRecord] = []
    for url in session.scalars(statement):
        crawls = list(session.scalars(select(Crawl).where(Crawl.url_id == url.id).order_by(Crawl.started_at.desc())))
        artifacts = list(
            session.scalars(
                select(Artifact)
                .where(Artifact.url_id == url.id, Artifact.kind.in_(BYTE_ARTIFACT_KINDS))
                .order_by(Artifact.created_at.desc())
            )
        )
        latest_crawl = crawls[0] if crawls else None
        latest_artifact = artifacts[0] if artifacts else None
        crawl_warning_count = sum(int((crawl.warnings_json or {}).get("count") or 0) for crawl in crawls)
        artifact_warning_count = sum(warning_count(artifact) for artifact in artifacts)
        rows.append(
            UrlListRecord(
                id=url.id,
                url=url.url,
                normalized_url=url.normalized_url,
                scheme=url.scheme,
                host=url.host,
                domain=url.domain,
                path=url.path,
                query=url.query,
                query_fingerprint=url.query_fingerprint,
                crawl_count=len(crawls),
                artifact_count=len(artifacts),
                active_artifact_count=sum(1 for artifact in artifacts if artifact.invalidated_at is None),
                invalidated_artifact_count=sum(1 for artifact in artifacts if artifact.invalidated_at is not None),
                cache_eligible_count=sum(1 for artifact in artifacts if is_cache_eligible(artifact)),
                latest_status_code=latest_crawl.status_code if latest_crawl else None,
                latest_crawl_at=latest_crawl.started_at if latest_crawl else None,
                latest_artifact_at=latest_artifact.created_at if latest_artifact else None,
                warning_count=crawl_warning_count + artifact_warning_count,
                crawl_warning_count=crawl_warning_count,
                artifact_warning_count=artifact_warning_count,
            )
        )

    return rows


def count_urls(
    session: Session,
    *,
    url_pattern: str | None = None,
    domain: str | None = None,
) -> int:
    statement = select(func.count()).select_from(Url)
    if url_pattern:
        statement = statement.where(Url.normalized_url.ilike(_sql_like_from_glob(url_pattern), escape="\\"))
    if domain:
        statement = statement.where(Url.domain.ilike(f"%{domain}%"))

    return int(session.scalar(statement) or 0)


def get_url(session: Session, url_id: UUID) -> Url | None:
    return session.get(Url, url_id)
