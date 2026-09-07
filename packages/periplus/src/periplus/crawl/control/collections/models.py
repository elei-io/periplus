"""Current collection intent and accounting; historical outcomes belong in DuckLake."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from periplus.platform.postgres.base import Base
from periplus.platform.postgres.types import json_type, utc_now


class CollectionRecord(Base):
    __tablename__ = "collections"
    __table_args__ = (
        CheckConstraint("page_limit > 0 AND reserved >= 0 AND consumed >= 0 "
                        "AND reserved + consumed <= page_limit", name="ck_collection_budget"),
        CheckConstraint("status IN ('active', 'paused', 'settled')", name="ck_collection_status"),
        Index("ix_collection_service", "status", "service_after", "service_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    spec: Mapped[dict[str, Any]] = mapped_column(json_type)
    status: Mapped[str] = mapped_column(Text, default="active")
    retiring: Mapped[bool] = mapped_column(default=False)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    scheduling_turn: Mapped[int] = mapped_column(Integer, default=0)
    page_limit: Mapped[int] = mapped_column(Integer)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    consumed: Mapped[int] = mapped_column(Integer, default=0)
    seeds_settled: Mapped[bool] = mapped_column(default=False)
    seed_provenance: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    admission_timing: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    discovery_state: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    selection_checkpoint: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    waiting_reason: Mapped[str | None] = mapped_column(Text)
    service_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    service_token: Mapped[UUID | None] = mapped_column()
    service_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    service_failures: Mapped[int] = mapped_column(default=0)
    outcome: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_dispatch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
