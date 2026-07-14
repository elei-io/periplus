from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class CrawlProfile(Base):
    __tablename__ = "crawl_profiles"
    __table_args__ = (
        CheckConstraint(
            "transport IN ('http', 'browser', 'firecrawl')",
            name="ck_crawl_profiles_transport",
        ),
        CheckConstraint("cost_rank >= 0", name="ck_crawl_profiles_cost_rank"),
        Index(
            "uq_crawl_profiles_trial_cost",
            "cost_rank",
            unique=True,
            postgresql_where=text("trial_eligible"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    transport: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    cost_rank: Mapped[int] = mapped_column(Integer)
    trial_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    policies = relationship("CrawlPolicy", back_populates="profile")


class CrawlPolicy(Base):
    __tablename__ = "crawl_policies"
    __table_args__ = (
        CheckConstraint(
            "scheme IN ('*', 'http', 'https')",
            name="ck_crawl_policies_scheme",
        ),
        CheckConstraint(
            "path_mode IN ('exact', 'prefix')",
            name="ck_crawl_policies_path_mode",
        ),
        CheckConstraint("max_concurrency >= 1", name="ck_crawl_policies_concurrency"),
        UniqueConstraint(
            "scheme", "host", "path_prefix", "path_mode",
            name="uq_crawl_policies_match",
        ),
        Index("ix_crawl_policies_enabled", "enabled"),
        Index("ix_crawl_policies_host", "host"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    slug: Mapped[str] = mapped_column(
        Text, unique=True, default=lambda: f"policy-{uuid4().hex[:12]}"
    )
    scheme: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(Text)
    path_prefix: Mapped[str] = mapped_column(Text, default="/")
    path_mode: Mapped[str] = mapped_column(Text, default="prefix")
    profile_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("crawl_profiles.id", ondelete="RESTRICT"),
    )
    max_concurrency: Mapped[int] = mapped_column(Integer, default=4)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    profile = relationship("CrawlProfile", back_populates="policies")
