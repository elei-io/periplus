from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class CatalogueMaterialization(Base):
    __tablename__ = "catalogue_materializations"
    __table_args__ = (
        CheckConstraint(
            "scope_kind IN ('url', 'document', 'crawl')",
            name="ck_catalogue_materializations_scope",
        ),
        CheckConstraint(
            "scope_column <> ''",
            name="ck_catalogue_materializations_scope_column",
        ),
        CheckConstraint(
            "backfill_scopes_per_minute > 0",
            name="ck_catalogue_materializations_positive_backfill_rate",
        ),
        CheckConstraint(
            "source_state IN ('current', 'source_changed')",
            name="ck_catalogue_materializations_source_state",
        ),
        Index("ix_catalogue_materializations_archived_at", "archived_at"),
        Index(
            "uq_catalogue_materializations_active_name",
            "name",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index(
            "uq_catalogue_materializations_active_view",
            "view_reference_id",
            unique=True,
            postgresql_where=text(
                "view_reference_id IS NOT NULL AND archived_at IS NULL"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sql: Mapped[str] = mapped_column(Text)
    view_reference_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalogue_view_references.id"),
    )
    bound_ducklake_view_uuid: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    source_state: Mapped[str] = mapped_column(Text, default="current")
    definition_revision_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), default=uuid4)
    scope_kind: Mapped[str] = mapped_column(Text)
    scope_column: Mapped[str] = mapped_column(Text)
    activation_snapshot: Mapped[int] = mapped_column(BigInteger)
    live_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    backfill_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    backfill_scopes_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    partition_column: Mapped[str | None] = mapped_column(Text, nullable=True)
    ducklake_table_uuid: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    last_refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dematerialization_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
