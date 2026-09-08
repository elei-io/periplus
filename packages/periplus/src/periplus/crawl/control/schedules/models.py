from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column
from periplus.platform.postgres.base import Base
from periplus.platform.postgres.types import json_type, utc_now


class RequestDefinitionRecord(Base):
    __tablename__ = "request_definitions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text)
    specification: Mapped[dict] = mapped_column(json_type)
    priority: Mapped[int] = mapped_column(default=0)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ScheduleRecord(Base):
    __tablename__ = "request_schedules"
    __table_args__ = (Index("ix_request_schedule_due", "enabled", "next_at"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    definition_id: Mapped[UUID] = mapped_column(ForeignKey("request_definitions.id"))
    configuration: Mapped[dict] = mapped_column(json_type)
    enabled: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=1)
    execution_count: Mapped[int] = mapped_column(default=0)
    next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_request_id: Mapped[UUID | None] = mapped_column()
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
