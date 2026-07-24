from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    JSON,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class CatalogueMaterialization(Base):
    """One immutable materialization incarnation and its desired lifecycle."""

    __tablename__ = "catalogue_materializations"
    __table_args__ = (
        CheckConstraint(
            "desired_state IN ('live', 'paused', 'deleting')",
            name="ck_catalogue_materializations_desired_state",
        ),
        CheckConstraint(
            "observed_state IN "
            "('creating', 'backfilling', 'live', 'paused', 'deleting', "
            "'blocked_schema', 'failed')",
            name="ck_catalogue_materializations_observed_state",
        ),
        CheckConstraint(
            "refresh_delay_seconds >= 0",
            name="ck_catalogue_materializations_refresh_delay",
        ),
        CheckConstraint(
            "refresh_strategy IN ('keyed', 'append', 'full')",
            name="ck_catalogue_materializations_refresh_strategy",
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
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    # The row id is the incarnation id. Dematerialize/rematerialize creates a new id.
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sql: Mapped[str] = mapped_column(Text)
    view_reference_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("catalogue_view_references.id")
    )
    source_view_uuid: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    source_table: Mapped[str] = mapped_column(Text)
    source_table_id: Mapped[int] = mapped_column(BigInteger)
    source_table_uuid: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    control_snapshot: Mapped[int] = mapped_column(BigInteger)
    desired_state: Mapped[str] = mapped_column(Text, default="live")
    observed_state: Mapped[str] = mapped_column(Text, default="creating")
    nats_consumer_name: Mapped[str] = mapped_column(Text, unique=True)
    refresh_delay_seconds: Mapped[float] = mapped_column(Float, default=1.0)
    refresh_strategy: Mapped[str] = mapped_column(Text)
    key_columns: Mapped[list[str]] = mapped_column(JSON)
    partition_column: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_table_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ducklake_table_uuid: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    bootstrap_snapshot: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bootstrap_partition_count: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    bootstrap_partition_cursor: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    processed_snapshot: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
