from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifacts_task_run_id", "task_run_id"),
        Index("ix_artifacts_url_id", "url_id"),
        Index("ix_artifacts_crawl_id", "crawl_id"),
        Index("ix_artifacts_kind", "kind"),
        Index("ix_artifacts_input_hash", "input_hash"),
        Index("ix_artifacts_sha256", "sha256"),
        Index("ix_artifacts_invalidated_at", "invalidated_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    crawl_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("crawls.id", ondelete="SET NULL"),
        nullable=True,
    )
    url_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("urls.id", ondelete="SET NULL"),
        nullable=True,
    )
    task_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("task_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    warnings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    task_run = relationship(
        "TaskRun",
        back_populates="artifacts",
        foreign_keys=[task_run_id],
    )
    crawl = relationship(
        "Crawl",
        back_populates="artifacts",
        foreign_keys=[crawl_id],
    )
    url = relationship(
        "Url",
        back_populates="artifacts",
        foreign_keys=[url_id],
    )
    task_run_usages = relationship(
        "TaskRunArtifact",
        back_populates="artifact",
        foreign_keys="TaskRunArtifact.artifact_id",
    )
