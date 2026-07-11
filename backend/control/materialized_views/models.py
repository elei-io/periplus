from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class MaterializedView(Base):
    __tablename__ = "materialized_views"
    __table_args__ = (Index("ix_materialized_views_archived_at", "archived_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    query_revision_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalogue_query_revisions.id"),
        nullable=True,
    )
    source_view_reference_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalogue_view_references.id"),
        nullable=True,
    )
    definition_revision_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), default=uuid4)
    refresh_mode: Mapped[str] = mapped_column(Text, default="full")
    scope_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_column: Mapped[str | None] = mapped_column(Text, nullable=True)
    activation_snapshot: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    live_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    backfill_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    backfill_scopes_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    partition_column: Mapped[str | None] = mapped_column(Text, nullable=True)
    ducklake_table_uuid: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    last_refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
