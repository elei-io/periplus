from __future__ import annotations

from datetime import UTC, datetime
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
    created_by_task_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    updated_by_task_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
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
