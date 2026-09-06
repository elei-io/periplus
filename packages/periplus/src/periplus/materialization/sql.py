"""Trusted SQL literal helpers for fixed Periplus materializations."""

from __future__ import annotations

from datetime import datetime

MERGE_BATCH_SIZE = 250
SQL_ID_BATCH_SIZE = 500


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_string_list(values: set[str]) -> str:
    return ", ".join(sql_string(value) for value in sorted(values))


def sql_nullable_string(value: object) -> str:
    return "NULL" if value is None else sql_string(str(value))


def sql_nullable_integer(value: object) -> str:
    return "NULL" if value is None else str(int(value))


def sql_timestamp(value: object) -> str:
    if not isinstance(value, datetime):
        raise TypeError("timestamp SQL values must be datetime instances")
    return f"TIMESTAMPTZ {sql_string(value.isoformat())}"


def row_batches(
    rows: list[dict[str, object]],
) -> list[list[dict[str, object]]]:
    return [
        rows[index : index + MERGE_BATCH_SIZE]
        for index in range(0, len(rows), MERGE_BATCH_SIZE)
    ]


def value_batches(
    values: list[str],
    size: int = SQL_ID_BATCH_SIZE,
) -> list[list[str]]:
    return [
        values[index : index + SQL_ID_BATCH_SIZE]
        for index in range(0, len(values), SQL_ID_BATCH_SIZE)
    ]
