"""Postgres-owned write exclusion, retirement receipts and reclamation work."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from periplus.platform.postgres import Base


class LakeWriteClaimRecord(Base):
    __tablename__ = "lake_write_claims"

    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    identity: Mapped[str] = mapped_column(Text, primary_key=True)
    owner: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RetiredEvidenceRecord(Base):
    __tablename__ = "retired_evidence"

    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    identity: Mapped[str] = mapped_column(Text, primary_key=True)
    retired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RetentionObjectRecord(Base):
    __tablename__ = "retention_objects"

    object_key: Mapped[str] = mapped_column(Text, primary_key=True)
    content_sha256: Mapped[str] = mapped_column(Text, index=True)
    stored_bytes: Mapped[int] = mapped_column(BigInteger)
    retired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    retired_snapshot: Mapped[int] = mapped_column(BigInteger, default=-1)
    snapshots_cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retirement_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True)
