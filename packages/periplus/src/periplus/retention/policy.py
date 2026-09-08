"""One request protection period, measured from terminal completion."""
from datetime import datetime, timedelta


def expires_at(retention_seconds: int | None, completed_at: datetime | None) -> datetime | None:
    if retention_seconds is None or completed_at is None:
        return None
    return completed_at + timedelta(seconds=retention_seconds)


# Missing definitions, invalid/absent durations and missing outcomes protect.
# This expression is used only on a joined, frozen request definition/outcome.
EXPIRED_SQL = """(
    json_type(c.specification, '$.retention_seconds') IN ('UBIGINT', 'BIGINT')
    AND TRY_CAST(json_extract_string(c.specification, '$.retention_seconds') AS BIGINT) BETWEEN 1 AND 315360000
    AND o.recorded_at IS NOT NULL
    AND o.recorded_at + TRY_CAST(json_extract_string(c.specification, '$.retention_seconds') AS BIGINT) * INTERVAL '1 second' <= ?
)"""
