"""Postgres authority for bounded materialization maintenance runs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from db import Base
from db.types import json_type, utc_now
from sqlalchemy import BigInteger, CheckConstraint, DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column


class MaterializationRunRecord(Base):
    __tablename__ = "materialization_runs"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('backfill', 'rebuild')",
            name="ck_materialization_runs_mode",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_materialization_runs_status",
        ),
        CheckConstraint(
            "item_budget >= 1 AND byte_budget >= 1",
            name="ck_materialization_runs_budgets",
        ),
        CheckConstraint(
            "source_items >= 0 AND source_bytes >= 0 AND output_rows >= 0",
            name="ck_materialization_runs_counts",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    mode: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="queued", index=True)
    requested_stages: Mapped[list[str]] = mapped_column(json_type)
    stages: Mapped[list[str]] = mapped_column(json_type)
    projector_versions: Mapped[dict[str, int]] = mapped_column(json_type)
    source_snapshot: Mapped[int] = mapped_column(BigInteger)
    catchup_snapshot: Mapped[int] = mapped_column(BigInteger)
    catchup_target_snapshot: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    catchup_stage: Mapped[int] = mapped_column(Integer, default=0)
    catchup_cursors: Mapped[dict[str, str | None]] = mapped_column(
        json_type, default=dict
    )
    current_stage: Mapped[int] = mapped_column(Integer, default=0)
    cursors: Mapped[dict[str, str | None]] = mapped_column(
        json_type, default=dict
    )
    destinations: Mapped[dict[str, str]] = mapped_column(
        json_type, default=dict
    )
    item_budget: Mapped[int] = mapped_column(Integer)
    byte_budget: Mapped[int] = mapped_column(BigInteger)
    source_items: Mapped[int] = mapped_column(BigInteger, default=0)
    source_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    output_rows: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
