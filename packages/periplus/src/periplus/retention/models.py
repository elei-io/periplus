"""Postgres-owned write exclusion, retirement receipts and reclamation work."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from periplus.platform.postgres import Base


class WriteClaimRecord(Base):
    __tablename__ = "write_claims"
    __table_args__ = {"schema": "state"}

    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    identity: Mapped[str] = mapped_column(Text, primary_key=True)
    owner: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CaptureRetirementRecord(Base):
    __tablename__ = "capture_retirements"
    __table_args__ = {"schema": "state"}
    capture_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
