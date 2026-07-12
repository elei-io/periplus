from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urldefrag, urlsplit, urlunsplit
from uuid import UUID, uuid4

from db import Base
from sqlalchemy import Boolean, DateTime, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


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

    crawl_policies = relationship(
        "CrawlPolicy",
        back_populates="url_match",
        foreign_keys="CrawlPolicy.url_match_id",
    )


def normalize_url(value: str) -> str:
    url, _ = urldefrag(value.strip())
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError(f"URL must be absolute HTTP(S): {value}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not supported")
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    netloc = (
        host
        if port is None or (scheme, port) in {("http", 80), ("https", 443)}
        else f"{host}:{port}"
    )
    query = urlencode(
        sorted(parse_qsl(parsed.query, keep_blank_values=True)),
        doseq=True,
    )
    return urlunsplit((scheme, netloc, parsed.path or "/", query, ""))
