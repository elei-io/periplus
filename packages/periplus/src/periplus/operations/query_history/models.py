from datetime import datetime
from uuid import UUID
from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from periplus.platform.postgres.base import Base
from periplus.platform.postgres.types import json_type

class QueryExecution(Base):
    __tablename__ = "query_executions"
    __table_args__ = (
        Index("ix_query_executions_started", "started_at"),
        Index("ix_query_executions_pattern_started", "query_fingerprint", "started_at"),
    )
    execution_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    request_id: Mapped[UUID | None] = mapped_column(Uuid)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(16))
    sql_text: Mapped[str] = mapped_column(Text)
    parameters: Mapped[list] = mapped_column(json_type)
    query_template: Mapped[str | None] = mapped_column(Text)
    query_fingerprint: Mapped[str | None] = mapped_column(String(64))
    fingerprint_version: Mapped[str] = mapped_column(String(80))
    relations: Mapped[list] = mapped_column(json_type)
    functions: Mapped[list] = mapped_column(json_type)
    features: Mapped[dict] = mapped_column(json_type)
    outcome: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(80))
    elapsed_ms: Mapped[float] = mapped_column(Float)
    result_rows: Mapped[int | None] = mapped_column(BigInteger)
    result_bytes: Mapped[int | None] = mapped_column(BigInteger)
    truncated: Mapped[bool | None] = mapped_column(Boolean)
    source_snapshot: Mapped[int | None] = mapped_column(BigInteger)
    service_version: Mapped[str | None] = mapped_column(String(200))
