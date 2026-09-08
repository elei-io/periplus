"""Safe query failures: native storage messages may contain credentials."""
from typing import Literal

import duckdb
from pydantic import BaseModel

from periplus.query.helpers import safe_helper_error
from periplus.query.service import BusyError


class QueryError(BaseModel):
    code: Literal["sql_invalid", "helper_limit", "resource_limit", "storage_unavailable", "service_busy", "query_failed"]
    detail: str


def query_error(error: Exception) -> tuple[int, QueryError]:
    if isinstance(error, BusyError):
        return 429, QueryError(code="service_busy", detail="Query server is busy. Try again shortly.")
    if isinstance(error, (TimeoutError, duckdb.InterruptException)):
        return 408, QueryError(code="resource_limit", detail="Query time limit exceeded. Reduce the work before retrying.")
    if isinstance(error, duckdb.OutOfMemoryException):
        return 422, QueryError(code="resource_limit", detail="Query memory or spill limit exceeded. Reduce the work before retrying.")
    if isinstance(error, (duckdb.HTTPException, duckdb.IOException)):
        return 503, QueryError(code="storage_unavailable", detail="Lake storage could not be read. Changing SQL will not repair unavailable storage; try again after the service recovers.")
    if isinstance(error, ValueError):
        return 422, QueryError(code="sql_invalid", detail=str(error))
    if isinstance(error, duckdb.Error) and (detail := safe_helper_error(str(error))):
        return 422, QueryError(code="helper_limit", detail=detail)
    if isinstance(error, duckdb.ParserException):
        return 422, QueryError(code="sql_invalid", detail="SQL syntax could not be parsed. Use AS for column aliases and double-quote reserved identifiers. Check commas, parentheses and DuckDB syntax.")
    if isinstance(error, (duckdb.BinderException, duckdb.CatalogException, duckdb.ConversionException, duckdb.InvalidInputException)):
        return 422, QueryError(code="sql_invalid", detail="SQL could not be bound or evaluated. Check names, argument types, casts, and the public schema.")
    return 500, QueryError(code="query_failed", detail="The query service could not complete this operation.")
