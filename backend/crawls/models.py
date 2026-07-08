from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Crawl(Base):
    __tablename__ = "crawls"
    __table_args__ = (
        Index("ix_crawls_url_id", "url_id"),
        Index("ix_crawls_task_run_id", "task_run_id"),
        Index("ix_crawls_started_at", "started_at"),
        Index("ix_crawls_success", "success"),
        Index("ix_crawls_status_code", "status_code"),
        Index("ix_crawls_input_hash", "input_hash"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    url_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("urls.id", ondelete="CASCADE"))
    task_run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inputs_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    input_hash: Mapped[str] = mapped_column(Text)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    redirects_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    errors_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    warnings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    url = relationship("Url", back_populates="crawls", foreign_keys=[url_id])
    task_run = relationship("TaskRun", back_populates="crawls", foreign_keys=[task_run_id])
    artifacts = relationship("Artifact", back_populates="crawl", foreign_keys="Artifact.crawl_id")
    task_run_usages = relationship("TaskRunCrawl", back_populates="crawl", foreign_keys="TaskRunCrawl.crawl_id")
