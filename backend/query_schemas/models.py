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


class QuerySchema(Base):
    __tablename__ = "query_schemas"
    __table_args__ = (
        Index("ix_query_schemas_enabled", "enabled"),
        Index("ix_query_schemas_match", "match"),
        Index("ix_query_schemas_domain", "domain"),
        Index("ix_query_schemas_schema_type", "schema_type"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    identity_key: Mapped[str] = mapped_column(Text, unique=True)
    url_match_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("url_matches.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    match: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    schema_type: Mapped[str] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    params_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    evidence_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    schema_hash: Mapped[str] = mapped_column(Text)
    generated_from_crawl_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    generated_from_document_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_by_task_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("task_runs.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    inputs_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    warnings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    generated_by_task_run = relationship("TaskRun", foreign_keys=[generated_by_task_run_id])
    url_match = relationship("UrlMatch", back_populates="query_schemas", foreign_keys=[url_match_id])
