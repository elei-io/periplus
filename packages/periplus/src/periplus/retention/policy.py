"""One request protection period, measured from terminal completion."""

from datetime import datetime, timedelta


def expires_at(
    retention_seconds: int | None, completed_at: datetime | None
) -> datetime | None:
    if retention_seconds is None or completed_at is None:
        return None
    return completed_at + timedelta(seconds=retention_seconds)
