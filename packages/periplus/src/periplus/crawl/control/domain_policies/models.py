from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from periplus.platform.postgres import Base
from periplus.platform.postgres.types import utc_now


class DomainPolicy(Base):
    """Host-matched website politeness independent of browser-fleet capacity."""

    __tablename__ = "domain_policies"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_domain_policies_version"),
        CheckConstraint("maximum_concurrency >= 1", name="ck_domain_policies_concurrency"),
        CheckConstraint("minimum_request_interval_seconds >= 0", name="ck_domain_policies_interval"),
        Index("ix_domain_policies_enabled", "enabled"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    host_match: Mapped[str] = mapped_column(Text, unique=True)
    maximum_concurrency: Mapped[int] = mapped_column(Integer, default=4)
    minimum_request_interval_seconds: Mapped[float] = mapped_column(default=0.0)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(Text, default="setup")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
