from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class CatalogueQuery(Base):
    __tablename__ = "catalogue_queries"
    __table_args__ = (Index("ix_catalogue_queries_archived_at", "archived_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_revision_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalogue_query_revisions.id"),
        nullable=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    revisions: Mapped[list[CatalogueQueryRevision]] = relationship(
        back_populates="query",
        foreign_keys="CatalogueQueryRevision.query_id",
        order_by="CatalogueQueryRevision.revision",
    )


class CatalogueQueryRevision(Base):
    __tablename__ = "catalogue_query_revisions"
    __table_args__ = (
        UniqueConstraint("query_id", "revision", name="uq_catalogue_query_revision_number"),
        UniqueConstraint("query_id", "id", name="uq_catalogue_query_revision_identity"),
        Index("ix_catalogue_query_revisions_query_id", "query_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    query_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("catalogue_queries.id", ondelete="CASCADE")
    )
    revision: Mapped[int] = mapped_column(Integer)
    sql: Mapped[str] = mapped_column(Text)
    sql_hash: Mapped[str] = mapped_column(Text)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    query: Mapped[CatalogueQuery] = relationship(
        back_populates="revisions", foreign_keys=[query_id]
    )
