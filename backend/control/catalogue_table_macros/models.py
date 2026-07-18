from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, JSON, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class CatalogueTableMacroDefinition(Base):
    __tablename__ = "catalogue_table_macros"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_catalogue_table_macro_slug"),
        UniqueConstraint(
            "schema_name", "macro_name", name="uq_catalogue_table_macro_name"
        ),
        UniqueConstraint("fixture_path", name="uq_catalogue_table_macros_fixture_path"),
        Index("ix_catalogue_table_macros_updated_at", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    schema_name: Mapped[str] = mapped_column(Text)
    macro_name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameters: Mapped[list[str]] = mapped_column(JSON)
    parameter_defaults: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    sql: Mapped[str] = mapped_column(Text)
    definition_revision_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), default=uuid4
    )
    fixture_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_from_query_revision_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalogue_query_revisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
