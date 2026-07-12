from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urldefrag, urlparse, urlunparse
from uuid import UUID, uuid4

from db import Base
from sqlalchemy import Boolean, DateTime, Index, Integer, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class UrlMatch(Base):
    __tablename__ = "url_matches"
    __table_args__ = (
        UniqueConstraint(
            "scheme",
            "host",
            "path_pattern",
            "match_type",
            "query_policy",
            name="uq_url_matches_identity",
        ),
        Index("ix_url_matches_host", "host"),
        Index("ix_url_matches_domain", "domain"),
        Index("ix_url_matches_path_pattern", "path_pattern"),
        Index("ix_url_matches_enabled", "enabled"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    scheme: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text)
    path_pattern: Mapped[str] = mapped_column(Text)
    match_type: Mapped[str] = mapped_column(Text, default="exact")
    query_policy: Mapped[str] = mapped_column(Text, default="ignore")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    query_schemas = relationship(
        "QuerySchema",
        back_populates="url_match",
        foreign_keys="QuerySchema.url_match_id",
    )
    crawl_policies = relationship(
        "CrawlPolicy",
        back_populates="url_match",
        foreign_keys="CrawlPolicy.url_match_id",
    )


def normalize_url(value: str) -> str:
    url, _ = urldefrag(value.strip())
    parsed = urlparse(url)
    query = urlencode(
        sorted(parse_qsl(parsed.query, keep_blank_values=True)),
        doseq=True,
    )
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            "",
            query,
            "",
        )
    )


def query_fingerprint(value: str) -> str | None:
    parsed = urlparse(value)
    if not parsed.query:
        return None
    query = urlencode(
        sorted(parse_qsl(parsed.query, keep_blank_values=True)),
        doseq=True,
    )
    return sha256(query.encode()).hexdigest()


def _resolve_url_match(
    session: Session,
    value: str,
    *,
    path_pattern: str,
    match_type: str,
) -> UrlMatch:
    parsed = urlparse(normalize_url(value))
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
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
        return existing

    url_match = UrlMatch(
        scheme=scheme,
        host=host,
        domain=host.removeprefix("www."),
        path_pattern=path_pattern,
        match_type=match_type,
        query_policy=query_policy,
    )
    session.add(url_match)
    session.flush()
    return url_match


def resolve_url_match_for_url(
    session: Session,
    value: str,
) -> UrlMatch:
    path = urlparse(normalize_url(value)).path or "/"
    return _resolve_url_match(
        session,
        value,
        path_pattern=path,
        match_type="exact",
    )


def resolve_domain_url_match_for_url(
    session: Session,
    value: str,
) -> UrlMatch:
    return _resolve_url_match(
        session,
        value,
        path_pattern="/*",
        match_type="glob",
    )
