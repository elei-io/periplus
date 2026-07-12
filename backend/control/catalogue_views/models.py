from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class CatalogueViewReference(Base):
    __tablename__ = "catalogue_view_references"
    __table_args__ = (
        UniqueConstraint("schema_name", "view_name", name="uq_catalogue_view_reference_name"),
        CheckConstraint("provisioned_by IN ('user', 'system')", name="ck_catalogue_view_references_provisioned_by"),
        Index("ix_catalogue_view_references_archived_at", "archived_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ducklake_view_uuid: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True)
    schema_name: Mapped[str] = mapped_column(Text)
    view_name: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    provisioned_by: Mapped[str] = mapped_column(Text, default="user", server_default=text("'user'"))
    created_from_query_revision_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalogue_query_revisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
