"""Shared SQLAlchemy value factories and portable column types."""

from datetime import UTC, datetime

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB


def utc_now() -> datetime:
    return datetime.now(UTC)


json_type = JSON().with_variant(JSONB(), "postgresql")
