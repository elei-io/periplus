"""Postgres control state for complete materialization rebuilds."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from periplus.platform.postgres import Base
from periplus.platform.postgres.types import json_type, utc_now
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column


class MaterializationRunRecord(Base):
    __tablename__ = "materialization_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'planning', 'running', 'activating', "
            "'completed', 'failed')",
            name="ck_materialization_runs_status",
        ),
        CheckConstraint(
            "batch_size >= 1",
            name="ck_materialization_runs_batch_size",
        ),
        CheckConstraint(
            "total_batches >= 0 AND completed_batches >= 0 "
            "AND completed_batches <= total_batches",
            name="ck_materialization_runs_batches",
        ),
        CheckConstraint(
            "source_items >= 0 AND source_bytes >= 0 AND output_rows >= 0 "
            "AND output_bytes >= 0",
            name="ck_materialization_runs_counts",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    status: Mapped[str] = mapped_column(Text, default="queued", index=True)
    source_snapshot: Mapped[int] = mapped_column(BigInteger)
    covered_snapshot: Mapped[int] = mapped_column(BigInteger)
    activation_snapshot: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    generation_tables: Mapped[dict[str, str]] = mapped_column(
        json_type, default=dict
    )
    registry_digest: Mapped[str] = mapped_column(Text)
    batch_size: Mapped[int] = mapped_column(Integer)
    total_batches: Mapped[int] = mapped_column(Integer, default=0)
    completed_batches: Mapped[int] = mapped_column(Integer, default=0)
    source_items: Mapped[int] = mapped_column(BigInteger, default=0)
    source_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    output_rows: Mapped[int] = mapped_column(BigInteger, default=0)
    output_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    plan_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    activation_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cleanup_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class MaterializationBatchRecord(Base):
    __tablename__ = "materialization_batches"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "ordinal",
            name="uq_materialization_batches_run_ordinal",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed')",
            name="ck_materialization_batches_status",
        ),
        CheckConstraint(
            "ordinal >= 0 AND attempts >= 0",
            name="ck_materialization_batches_counters",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("materialization_runs.id", ondelete="CASCADE"),
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[int] = mapped_column(BigInteger)
    visit_ids: Mapped[list[str]] = mapped_column(json_type)
    status: Mapped[str] = mapped_column(Text, default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    write_intent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_items: Mapped[int] = mapped_column(BigInteger, default=0)
    source_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    output_rows: Mapped[int] = mapped_column(BigInteger, default=0)
    output_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MaterializationStateRecord(Base):
    __tablename__ = "materialization_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_materialization_state_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    generation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    covered_snapshot: Mapped[int] = mapped_column(BigInteger)
    batch_size: Mapped[int] = mapped_column(Integer)
    registry_digest: Mapped[str] = mapped_column(Text)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MaterializationAppliedBatchRecord(Base):
    __tablename__ = "materialization_applied_batches"

    batch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    source_snapshot: Mapped[int] = mapped_column(BigInteger)
    source_items: Mapped[int] = mapped_column(BigInteger)
    source_bytes: Mapped[int] = mapped_column(BigInteger)
    output_rows: Mapped[int] = mapped_column(BigInteger)
    output_bytes: Mapped[int] = mapped_column(BigInteger)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
