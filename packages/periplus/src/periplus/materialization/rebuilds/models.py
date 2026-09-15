"""Recreatable build execution state. Archive positions remain in object storage."""

from datetime import datetime
from uuid import UUID
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Uuid,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from periplus.platform.postgres.base import Base


class BuildRecord(Base):
    __tablename__ = "material_builds"
    __table_args__ = {"schema": "state"}
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    phase: Mapped[str] = mapped_column(String(24))
    recipe: Mapped[str] = mapped_column(String(64))
    manifest_key: Mapped[str | None] = mapped_column(String(256))
    material_database: Mapped[str] = mapped_column(String(64), unique=True)
    query_database: Mapped[str] = mapped_column(String(64), unique=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    page_size: Mapped[int] = mapped_column(Integer, default=32)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    protected: Mapped[bool] = mapped_column(Boolean, default=True)
    blocker: Mapped[str | None] = mapped_column(String(1000))
    verification_heads: Mapped[list | None] = mapped_column(JSON)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    drain_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RangeRecord(Base):
    __tablename__ = "material_build_ranges"
    __table_args__ = {"schema": "state"}
    build_id: Mapped[UUID] = mapped_column(
        ForeignKey("state.material_builds.id"), primary_key=True
    )
    shard: Mapped[int] = mapped_column(Integer, primary_key=True)
    upper: Mapped[int] = mapped_column(BigInteger)
    cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    live_cursor: Mapped[int] = mapped_column(BigInteger)
    processed: Mapped[int] = mapped_column(BigInteger, default=0)


class BatchRecord(Base):
    __tablename__ = "material_batches"
    __table_args__ = (
        UniqueConstraint("build_id", "shard", "lane", name="uq_material_active_range"),
        {"schema": "state"},
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    build_id: Mapped[UUID] = mapped_column(ForeignKey("state.material_builds.id"), index=True)
    shard: Mapped[int] = mapped_column(Integer)
    lane: Mapped[str] = mapped_column(String(16))
    start: Mapped[int] = mapped_column(BigInteger)
    end: Mapped[int] = mapped_column(BigInteger)
    build_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner: Mapped[UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(128))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(1000))


class PublicationRecord(Base):
    __tablename__ = "material_publications"
    __table_args__ = {"schema": "state"}
    api_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    build_id: Mapped[UUID] = mapped_column(ForeignKey("state.material_builds.id"))
    revision: Mapped[int] = mapped_column(Integer, default=0)
