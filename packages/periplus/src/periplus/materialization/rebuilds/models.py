"""Small control records; no corpus-sized success ledger."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from periplus.platform.postgres.base import Base


class BuildRecord(Base):
    __tablename__ = 'material_builds'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    phase: Mapped[str] = mapped_column(String(24))
    semantic_version: Mapped[str] = mapped_column(String(64))
    material_database: Mapped[str] = mapped_column(String(64), unique=True)
    query_database: Mapped[str] = mapped_column(String(64), unique=True)
    consumer: Mapped[str] = mapped_column(String(80), unique=True)
    stream_created: Mapped[str | None] = mapped_column(String(64))
    consumer_created: Mapped[str | None] = mapped_column(String(64))
    ingestion_created: Mapped[str | None] = mapped_column(String(64))
    barrier: Mapped[int | None] = mapped_column(BigInteger)
    ingestion_floor: Mapped[int] = mapped_column(BigInteger, default=0)
    material_floor: Mapped[int] = mapped_column(BigInteger, default=0)
    live_pending: Mapped[int] = mapped_column(BigInteger, default=0)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    protected: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    page_size: Mapped[int] = mapped_column(Integer, default=32)
    blocker: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    drain_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RangeRecord(Base):
    __tablename__ = 'material_build_ranges'
    build_id: Mapped[UUID] = mapped_column(ForeignKey('material_builds.id'), primary_key=True)
    month: Mapped[int] = mapped_column(Integer, primary_key=True)
    upper: Mapped[list] = mapped_column(JSON)
    cursor: Mapped[list | None] = mapped_column(JSON)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    processed: Mapped[int] = mapped_column(BigInteger, default=0)
    blocker: Mapped[str | None] = mapped_column(String(256))


class PublicationRecord(Base):
    __tablename__ = 'material_publications'
    api_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    build_id: Mapped[UUID] = mapped_column(ForeignKey('material_builds.id'))
    revision: Mapped[int] = mapped_column(Integer, default=0)
