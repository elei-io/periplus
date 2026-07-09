from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Url(Base):
    __tablename__ = "urls"
    __table_args__ = (
        Index("ix_urls_host", "host"),
        Index("ix_urls_domain", "domain"),
        Index("ix_urls_path", "path"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    url: Mapped[str] = mapped_column(Text, unique=True)
    normalized_url: Mapped[str] = mapped_column(Text, unique=True)
    scheme: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    query_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)

    crawls = relationship("Crawl", back_populates="url", foreign_keys="Crawl.url_id")
    artifacts = relationship("Artifact", back_populates="url", foreign_keys="Artifact.url_id")


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

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    scheme: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text)
    path_pattern: Mapped[str] = mapped_column(Text)
    match_type: Mapped[str] = mapped_column(Text, default="exact")
    query_policy: Mapped[str] = mapped_column(Text, default="ignore")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_by_task_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("task_runs.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_task_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("task_runs.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    query_schemas = relationship("QuerySchema", back_populates="url_match", foreign_keys="QuerySchema.url_match_id")
