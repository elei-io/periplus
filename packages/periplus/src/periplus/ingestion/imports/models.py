"""Import intent and checkpoints; raw capture evidence remains in object storage."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import DateTime, Integer, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from periplus.platform.postgres.base import Base


class ImportRecord(Base):
    __tablename__ = "archive_imports"
    __table_args__ = {"schema": "state"}
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    specification: Mapped[dict] = mapped_column(JSON)
    progress: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), index=True)
    error: Mapped[str | None] = mapped_column(String(1000))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
