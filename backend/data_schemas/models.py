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


class DataSchema(Base):
    __tablename__ = "data_schemas"
    __table_args__ = (
        Index("ix_data_schemas_enabled", "enabled"),
        Index("ix_data_schemas_match", "match"),
        Index("ix_data_schemas_domain", "domain"),
        Index("ix_data_schemas_prompt_hash", "prompt_hash"),
        Index("ix_data_schemas_schema_type", "schema_type"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    identity_key: Mapped[str] = mapped_column(Text, unique=True)
    match: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    prompt: Mapped[str] = mapped_column(Text)
    prompt_hash: Mapped[str] = mapped_column(Text)
    schema_type: Mapped[str] = mapped_column(Text)
    target_json_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    schema_hash: Mapped[str] = mapped_column(Text)
    generated_from_crawl_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("crawls.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    generated_from_artifact_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("artifacts.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    generated_by_task_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("task_runs.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    inputs_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    validation_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    generated_from_crawl = relationship("Crawl", foreign_keys=[generated_from_crawl_id])
    generated_from_artifact = relationship("Artifact", foreign_keys=[generated_from_artifact_id])
    generated_by_task_run = relationship("TaskRun", foreign_keys=[generated_by_task_run_id])
    task_runs = relationship("TaskRun", back_populates="data_schema", foreign_keys="TaskRun.data_schema_id")
