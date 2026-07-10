from __future__ import annotations
from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urldefrag, urlparse, urlunparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import UrlMatch


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


def resolve_domain_url_match_for_url(
    session: Session,
    value: str,
    *,
    task_run_id: UUID | None = None,
) -> UrlMatch:
    normalized = normalize_url(value)
    parsed = urlparse(normalized)
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    path_pattern = "/*"
    match_type = "glob"
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
