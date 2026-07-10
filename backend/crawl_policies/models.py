from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class CrawlPolicy(Base):
    __tablename__ = "crawl_policies"
    __table_args__ = (
        Index("ix_crawl_policies_enabled", "enabled"),
        Index("ix_crawl_policies_match", "match"),
        Index("ix_crawl_policies_url_match_id", "url_match_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    url_match_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("url_matches.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    match: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    url_match = relationship("UrlMatch", back_populates="crawl_policies", foreign_keys=[url_match_id])


class CrawlPermit(Base):
    __tablename__ = "crawl_permits"
    __table_args__ = (
        UniqueConstraint("permit_key", "slot", name="uq_crawl_permits_key_slot"),
        Index("ix_crawl_permits_lease", "leased_until"),
        Index("ix_crawl_permits_task_run", "task_run_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    permit_key: Mapped[str] = mapped_column(Text)
    slot: Mapped[int] = mapped_column(Integer)
    policy_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("crawl_policies.id", ondelete="CASCADE"), nullable=True
    )
    holder_worker_id: Mapped[str] = mapped_column(Text)
    task_run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="CASCADE")
    )
    url_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("urls.id", ondelete="SET NULL"), nullable=True
    )
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    leased_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
