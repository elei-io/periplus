"""Privileged, request-scoped DuckLake SQL for the operator console."""
from __future__ import annotations

import json
import threading
import time

import duckdb
from fastapi import APIRouter, Request
from pydantic import BaseModel, JsonValue
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.connection import DuckLakeConnectionFactory, _identifier
from periplus.query.service import MAX_RESPONSE_BYTES, MAX_ROWS, BusyError, QueryRequest, _json_value

router = APIRouter(prefix="/admin/sql", tags=["admin-sql"])


class AdminSqlResult(BaseModel):
    columns: list[str]
    types: list[str]
    rows: list[list[JsonValue]]
    truncated: bool
    elapsed_ms: float


class AdminSqlService:
    """One executing request per process; each script gets a fresh writable session."""

    def __init__(self, config: CatalogueConfig):
        self.factory = DuckLakeConnectionFactory(config)
        self._lock = threading.Lock()

    def execute(self, payload: QueryRequest) -> AdminSqlResult:
        if not self._lock.acquire(blocking=False):
            raise BusyError("An administrative SQL request is already running.")
        started = time.monotonic()
        connection = None
        try:
            connection = self.factory.connect()
            connection.execute(f"USE {_identifier(self.factory.config.alias)}")
            statements = connection.extract_statements(payload.sql)
            if not statements:
                raise ValueError("Enter at least one SQL statement.")
            if any(statement.type == duckdb.StatementType.TRANSACTION for statement in statements):
                raise ValueError("Each request owns its transaction. Omit BEGIN, COMMIT, and ROLLBACK.")
            if len(statements) > 1 and payload.parameters:
                raise ValueError("Parameters are supported only for a single statement.")
            connection.execute("BEGIN TRANSACTION")
            for statement in statements:
                cursor = connection.execute(statement, payload.parameters)
            description = cursor.description or []
            columns = [str(column[0]) for column in description]
            types = [str(column[1]) for column in description]
            rows: list[list[JsonValue]] = []
            size = len(json.dumps([columns, types]).encode()) + 1024
            if size > MAX_RESPONSE_BYTES:
                raise ValueError("Result column metadata exceeds the response limit.")
            truncated = False
            while (row := cursor.fetchone()) is not None:
                converted = [_json_value(value) for value in row]
                size += len(json.dumps(converted, ensure_ascii=True).encode()) + 1
                if len(rows) == MAX_ROWS or size > MAX_RESPONSE_BYTES:
                    truncated = True
                    break
                rows.append(converted)
            result = AdminSqlResult(
                columns=columns, types=types, rows=rows, truncated=truncated,
                elapsed_ms=(time.monotonic() - started) * 1000,
            )
            connection.execute("COMMIT")
            return result
        finally:
            # Closing also rolls back failed scripts and discards session settings/temp objects.
            try:
                if connection is not None:
                    connection.close()
            finally:
                self._lock.release()


@router.post("/exec", response_model=AdminSqlResult)
async def execute(payload: QueryRequest, request: Request):
    from periplus.query.history import track, result_fields
    async with track(request, payload, "execute", source="admin") as record:
        result = await _execute(payload, request)
        if isinstance(result, JSONResponse):
            record.update(outcome="rejected" if result.status_code in {403, 429} else "failed",
                          error_code=f"admin_sql_{result.status_code}")
        else:
            record.update(result_fields(result))
        return result


async def _execute(payload: QueryRequest, request: Request):
    if getattr(request.state, "api_role", None) != "admin":
        return JSONResponse({"detail": "Administrative access is required."}, status_code=403)
    slot = request.app.state.admin_sql_slot
    if slot.locked():
        return JSONResponse({"detail": "An administrative SQL request is already running."}, status_code=429, headers={"Retry-After": "1"})
    try:
        async with slot:
            return await run_in_threadpool(request.app.state.admin_sql.execute, payload)
    except BusyError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=429, headers={"Retry-After": "1"})
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except duckdb.Error as exc:
        # Admin SQL can reference service secrets. Do not return native errors containing them.
        return JSONResponse({"detail": f"Administrative SQL failed ({type(exc).__name__}). The request transaction was rolled back."}, status_code=422)
