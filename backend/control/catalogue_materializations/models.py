from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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
        ForeignKeyConstraint(
            ["query_id", "active_query_revision_id"],
            ["catalogue_query_revisions.query_id", "catalogue_query_revisions.id"],
            name="fk_catalogue_materializations_active_query_revision",
        ),
        CheckConstraint(
            "(query_id IS NOT NULL) <> (view_reference_id IS NOT NULL)",
            name="ck_catalogue_materializations_one_source",
        ),
        CheckConstraint(
            "(query_id IS NOT NULL AND active_query_revision_id IS NOT NULL "
            "AND view_reference_id IS NULL AND bound_ducklake_view_uuid IS NULL) "
            "OR (query_id IS NULL AND active_query_revision_id IS NULL "
            "AND view_reference_id IS NOT NULL AND bound_ducklake_view_uuid IS NOT NULL)",
            name="ck_catalogue_materializations_source_shape",
        ),
        CheckConstraint(
            "refresh_mode IN ('full', 'scope_incremental')",
            name="ck_catalogue_materializations_refresh_mode",
        ),
        CheckConstraint(
            "scope_kind IS NULL OR scope_kind = 'document'",
            name="ck_catalogue_materializations_document_scope",
        ),
        CheckConstraint(
            "(refresh_mode = 'full' AND scope_kind IS NULL AND scope_column IS NULL "
            "AND activation_snapshot IS NULL AND NOT live_enabled AND NOT backfill_enabled) "
            "OR (refresh_mode = 'scope_incremental' "
            "AND scope_kind = 'document' AND scope_column IS NOT NULL "
            "AND activation_snapshot IS NOT NULL)",
            name="ck_catalogue_materializations_mode_state",
        ),
        CheckConstraint(
            "backfill_scopes_per_minute > 0",
            name="ck_catalogue_materializations_positive_backfill_rate",
        ),
        CheckConstraint(
            "source_state IN ('current', 'source_changing', 'source_changed')",
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
            "uq_catalogue_materializations_active_query",
            "query_id",
            unique=True,
            postgresql_where=text("query_id IS NOT NULL AND archived_at IS NULL"),
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
    query_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("catalogue_queries.id"), nullable=True
    )
    active_query_revision_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    view_reference_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalogue_view_references.id"),
        nullable=True,
    )
    bound_ducklake_view_uuid: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    source_state: Mapped[str] = mapped_column(Text, default="current")
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
    dematerialization_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
