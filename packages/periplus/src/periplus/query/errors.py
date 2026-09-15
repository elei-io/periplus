"""Safe query failures: native storage messages may contain credentials."""

from typing import Literal

from periplus.platform.clickhouse import ClickHouseError
from pydantic import BaseModel

from periplus.query.service import BusyError, ResultLimitError


class QueryError(BaseModel):
    code: Literal[
        "sql_invalid",
        "helper_limit",
        "resource_limit",
        "storage_unavailable",
        "service_busy",
        "query_failed",
    ]
    detail: str


def query_error(error: Exception) -> tuple[int, QueryError]:
    if isinstance(error, ClickHouseError):
        if error.code in {"159", "394"}:
            return 408, QueryError(
                code="resource_limit",
                detail="Query execution exceeded its deadline or was cancelled.",
            )
        if error.code == "241":
            return 422, QueryError(
                code="resource_limit",
                detail="Query exceeded its memory budget (ClickHouse code 241).",
            )
        if error.code == "307":
            return 422, QueryError(
                code="resource_limit",
                detail="Query exceeded its scan-byte budget (ClickHouse code 307).",
            )
        if error.code in {"202", "396", "response_limit"}:
            return 422, QueryError(
                code="resource_limit",
                detail="Query exceeded its execution or result budget.",
            )
        if error.code in {"62", "47", "43", "44", "46", "53", "164", "497"}:
            return 422, QueryError(
                code="sql_invalid",
                detail="Query could not be evaluated against the public ClickHouse catalogue.",
            )
        return 503, QueryError(
            code="storage_unavailable",
            detail="ClickHouse could not complete the read; retry after the service recovers.",
        )
    if isinstance(error, ResultLimitError):
        return 422, QueryError(code="resource_limit", detail=str(error))
    if isinstance(error, BusyError):
        return 429, QueryError(
            code="service_busy", detail="Query server is busy. Try again shortly."
        )
    if isinstance(error, TimeoutError):
        return 408, QueryError(
            code="resource_limit", detail="Query time limit exceeded."
        )
    if isinstance(error, ValueError):
        return 422, QueryError(code="sql_invalid", detail=str(error))
    return 500, QueryError(
        code="query_failed",
        detail="The query service could not complete this operation.",
    )
