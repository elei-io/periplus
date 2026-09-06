from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from periplus.platform.postgres.base import Base
from periplus.platform.postgres.types import utc_now, json_type


class CoverageRequest(Base):
    __tablename__ = "coverage_requests"
    __table_args__ = (
        CheckConstraint("kind IN ('url', 'description')", name="coverage_request_kind"),
        CheckConstraint("status IN ('pending', 'resolving', 'ongoing', 'completed', 'failed')", name="coverage_request_status"),
        CheckConstraint("depth BETWEEN 0 AND 2", name="coverage_request_depth"),
        CheckConstraint("max_pages BETWEEN 1 AND 1000", name="coverage_request_max_pages"),
        CheckConstraint("link_scope IN ('internal', 'external', 'both')", name="coverage_request_scope"),
        Index("ix_coverage_requests_status_created", "status", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(Text)
    input: Mapped[str] = mapped_column(Text)
    depth: Mapped[int] = mapped_column(Integer)
    link_scope: Mapped[str] = mapped_column(Text)
    max_pages: Mapped[int] = mapped_column(Integer)
    allowed_sections: Mapped[list[str]] = mapped_column(json_type, default=list)
    status: Mapped[str] = mapped_column(Text, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_urls: Mapped[list[str]] = mapped_column(json_type, default=list)
    resolution: Mapped[dict] = mapped_column(json_type, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
