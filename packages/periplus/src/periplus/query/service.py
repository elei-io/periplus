"""Process-owned, bounded SQL preparation and execution over a read-only lake."""
from __future__ import annotations

import base64
from datetime import date, datetime, time as datetime_time
from decimal import Decimal
import json
import logging
import math
from pathlib import Path
import threading
import time
from uuid import UUID, uuid4

import duckdb
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlglot import exp

from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.connection import DuckLakeConnectionFactory, _identifier
from periplus.query.validation import _bounded_query, _one_statement

MAX_ROWS = 1000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
QUERY_SECONDS = 20
logger = logging.getLogger(__name__)


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str = Field(min_length=1, max_length=100_000)
    parameters: list[JsonValue] = Field(default_factory=list, max_length=100)


class Diagnostic(BaseModel):
    severity: str
    code: str
    message: str


class PreparedQuery(BaseModel):
    query_id: str
    sql: str
    parameters: list[JsonValue]
    diagnostics: list[Diagnostic]
    plan: str


class QueryResult(PreparedQuery):
    columns: list[str]
    types: list[str]
    rows: list[list[JsonValue]]
    truncated: bool
    elapsed_ms: float


class BusyError(Exception):
    pass


class QueryService:
    """One connection and admission slot; no unbounded request queue."""

    def __init__(self, config: CatalogueConfig, *, deadline: float = QUERY_SECONDS):
        self.deadline = deadline
        self._lock = threading.Lock()
        self.connection = DuckLakeConnectionFactory(config, duckdb_config={
            "threads": "2", "memory_limit": "512MB", "max_temp_directory_size": "256MB",
        }).connect(read_only=True)
        d = self.connection
        try:
            d.execute(f"USE {_identifier(config.alias)}")
            # Extensions and credentials are installed before locking the session.
            # Only lake data paths may perform filesystem IO after this point.
            root = config.data_path if "://" in config.data_path else str(Path(config.data_path).resolve())
            d.execute("SET allowed_directories = ?", [[root.rstrip('/') + '/']])
            d.execute("SET enable_external_access=false")
            d.execute("SET autoinstall_known_extensions=false")
            d.execute("SET autoload_known_extensions=false")
            d.execute("SET allow_community_extensions=false")
            d.execute("SET lock_configuration=true")
        except BaseException:
            d.close()
            raise

    def close(self):
        with self._lock:
            self.connection.close()

    def prepare(self, payload: QueryRequest) -> PreparedQuery:
        return self._run(payload, execute=False)

    def execute(self, payload: QueryRequest) -> QueryResult:
        return self._run(payload, execute=True)

    def _run(self, payload: QueryRequest, *, execute: bool):
        if not self._lock.acquire(blocking=False):
            raise BusyError("Query server is busy. Try again shortly.")
        started = time.monotonic()
        query_id = str(uuid4())
        expired = threading.Event()
        def interrupt():
            expired.set()
            self.connection.interrupt()
        timer = threading.Timer(self.deadline, interrupt)
        timer.start()
        logger.info("query_submitted %s", json.dumps({"query_id": query_id, "operation": "exec" if execute else "prep", **payload.model_dump()}))
        status = "failed"
        d = self.connection
        try:
            executable = _bounded_query(payload.sql, max_rows=MAX_ROWS)
            statement = _one_statement(payload.sql)
            diagnostics = []
            if any(join.args.get("kind") == "CROSS" for join in statement.find_all(exp.Join)):
                diagnostics.append(Diagnostic(severity="warning", code="cartesian_product", message="A Cartesian product can require substantial work."))
            d.execute("BEGIN TRANSACTION")
            # Plain EXPLAIN binds without running EXPLAIN ANALYZE's child.
            if payload.sql.lstrip().upper().startswith("EXPLAIN"):
                import re
                explained = re.sub(r"^\s*EXPLAIN\s+(?:ANALYZE\s+)?", "", payload.sql, flags=re.IGNORECASE)
                plan_sql = "EXPLAIN " + explained
            elif isinstance(statement, exp.Show):
                plan_sql = payload.sql
            else:
                plan_sql = "EXPLAIN " + payload.sql
            plan = "\n".join(str(row[-1]) for row in d.execute(plan_sql, payload.parameters).fetchall())
            if len(plan.encode()) > 64_000:
                plan = plan.encode()[:64_000].decode(errors="ignore")
                diagnostics.append(Diagnostic(severity="warning", code="plan_truncated", message="The execution plan preview was truncated."))
            prepared = PreparedQuery(query_id=query_id, sql=payload.sql, parameters=payload.parameters, diagnostics=diagnostics, plan=plan)
            if not execute:
                status = "prepared"
                return prepared
            cursor = d.execute(executable, payload.parameters)
            columns = [str(col[0]) for col in cursor.description]
            types = [str(col[1]) for col in cursor.description]
            rows = []
            size = len(prepared.model_dump_json().encode()) + len(json.dumps([columns, types]).encode()) + 1024
            truncated = False
            while True:
                row = cursor.fetchone()
                if row is None:
                    break
                converted = [_json_value(value) for value in row]
                size += len(json.dumps(converted, ensure_ascii=False).encode()) + 1
                if len(rows) == MAX_ROWS or size > MAX_RESPONSE_BYTES:
                    truncated = True
                    break
                if expired.is_set():
                    raise TimeoutError("Query time limit exceeded.")
                rows.append(converted)
            status = "completed"
            return QueryResult(**prepared.model_dump(), columns=columns, types=types, rows=rows, truncated=truncated, elapsed_ms=(time.monotonic()-started)*1000)
        except duckdb.InterruptException as exc:
            raise TimeoutError("Query time limit exceeded.") from exc
        finally:
            timer.cancel()
            timer.join()
            try:
                d.execute("ROLLBACK")
            except duckdb.TransactionException:
                pass
            finally:
                self._lock.release()
                logger.info("query_finished %s", json.dumps({"query_id": query_id, "status": status, "elapsed_ms": (time.monotonic()-started)*1000}))


def _json_value(value):
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53-1:
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)
