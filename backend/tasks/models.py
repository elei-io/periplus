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


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_enabled", "enabled"),
        Index("ix_tasks_next_run_at", "next_run_at"),
        Index("ix_tasks_primitive", "primitive"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text)
    primitive: Mapped[str] = mapped_column(Text)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    schedule_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    created_by_effect_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("effect_runs.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_effect_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("effect_runs.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    disabled_by_effect_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("effect_runs.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    effects: Mapped[list[TaskEffect]] = relationship(
        back_populates="task",
        foreign_keys="TaskEffect.task_id",
        cascade="all, delete-orphan",
    )
    runs: Mapped[list[TaskRun]] = relationship(
        back_populates="task",
        foreign_keys="TaskRun.task_id",
    )


class TaskEffect(Base):
    __tablename__ = "task_effects"
    __table_args__ = (
        Index("ix_task_effects_task_id", "task_id"),
        Index("ix_task_effects_enabled", "enabled"),
        Index("ix_task_effects_effect_type", "effect_type"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"))
    effect_type: Mapped[str] = mapped_column(Text)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    task: Mapped[Task] = relationship(
        back_populates="effects",
        foreign_keys=[task_id],
    )
    runs: Mapped[list[EffectRun]] = relationship(
        back_populates="effect",
        foreign_keys="EffectRun.effect_id",
    )


class TaskRun(Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        Index("ix_task_runs_task_id", "task_id"),
        Index("ix_task_runs_status", "status"),
        Index("ix_task_runs_queued_at", "queued_at"),
        Index("ix_task_runs_trigger_kind", "trigger_kind"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("tasks.id"))
    status: Mapped[str] = mapped_column(Text)
    trigger_kind: Mapped[str] = mapped_column(Text)
    triggered_by_effect_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("effect_runs.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )

    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    warnings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    artifacts_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    task: Mapped[Task] = relationship(
        back_populates="runs",
        foreign_keys=[task_id],
    )
    effect_runs: Mapped[list[EffectRun]] = relationship(
        back_populates="source_run",
        foreign_keys="EffectRun.source_run_id",
    )


class EffectRun(Base):
    __tablename__ = "effect_runs"
    __table_args__ = (
        Index("ix_effect_runs_effect_id", "effect_id"),
        Index("ix_effect_runs_source_run_id", "source_run_id"),
        Index("ix_effect_runs_status", "status"),
        Index("ix_effect_runs_operation", "operation"),
        Index("ix_effect_runs_target_task_id", "target_task_id"),
        Index("ix_effect_runs_target_run_id", "target_run_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    effect_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("task_effects.id"))
    source_run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("task_runs.id"))

    status: Mapped[str] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(Text)

    target_task_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    target_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("task_runs.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )

    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    effect: Mapped[TaskEffect] = relationship(
        back_populates="runs",
        foreign_keys=[effect_id],
    )
    source_run: Mapped[TaskRun] = relationship(
        back_populates="effect_runs",
        foreign_keys=[source_run_id],
    )
