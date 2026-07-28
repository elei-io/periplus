from __future__ import annotations

from datetime import datetime
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
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from atlas.platform.postgres import Base
from atlas.platform.postgres.types import json_type, utc_now


class CrawlSchedule(Base):
    __tablename__ = "crawl_schedules"
    __table_args__ = (
        UniqueConstraint(
            "graph_id", "name", name="uq_crawl_schedules_graph_name"
        ),
        CheckConstraint(
            "maximum_run_count IS NULL OR maximum_run_count >= 1",
            name="ck_crawl_schedules_maximum_run_count",
        ),
        CheckConstraint(
            "max_crawls >= 1 AND max_crawls <= 1000000",
            name="ck_crawl_schedules_max_crawls",
        ),
        CheckConstraint(
            "run_count >= 0", name="ck_crawl_schedules_run_count"
        ),
        CheckConstraint(
            "overlap_policy IN ('skip', 'allow')",
            name="ck_crawl_schedules_overlap_policy",
        ),
        CheckConstraint(
            "misfire_policy IN ('skip', 'run_once')",
            name="ck_crawl_schedules_misfire_policy",
        ),
        Index(
            "ix_crawl_schedules_due",
            "next_run_at",
            postgresql_where=text("enabled AND next_run_at IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    graph_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("crawl_graphs.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timing: Mapped[dict[str, Any]] = mapped_column(json_type)
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    maximum_run_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    max_crawls: Mapped[int] = mapped_column(Integer, default=1_000)
    root_url: Mapped[str] = mapped_column(Text)
    overlap_policy: Mapped[str] = mapped_column(Text, default="skip")
    misfire_policy: Mapped[str] = mapped_column(Text, default="skip")
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_occurrence_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
