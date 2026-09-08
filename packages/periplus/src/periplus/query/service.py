"""Process-owned, bounded SQL preparation and execution over a read-only lake."""
from __future__ import annotations

import base64
import hashlib
from datetime import date, datetime, time as datetime_time
from decimal import Decimal
import json
import logging
import math
from pathlib import Path
import threading
import time
from uuid import UUID, uuid4
from typing import Literal
from periplus.platform.catalogue.public import PUBLIC_SCHEMA

import duckdb
from prometheus_client import Gauge
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlglot import exp

from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.connection import DuckLakeConnectionFactory, _identifier
from periplus.query.validation import _bounded_query, _one_statement
from periplus.operations.access.schemas import QueryLimits
from periplus.operations.query_history.schemas import PreparationEvidence

COMPILER_VERSION = "public-query-v1"

logger = logging.getLogger(__name__)
_active_queries = Gauge("periplus_query_active_operations", "Occupied query admission slots.")


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str = Field(min_length=1, max_length=100_000)
    schema_version: Literal["public_v1"] = PUBLIC_SCHEMA
    parameters: list[JsonValue] = Field(default_factory=list, max_length=100)


class Diagnostic(BaseModel):
    severity: str
    code: str
    message: str


class PreparedQuery(BaseModel):
    schema_version: Literal["public_v1"] = PUBLIC_SCHEMA
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
    source_snapshot: int = Field(ge=0)


class BusyError(Exception):
    pass


class QueryService:
    """One connection and admission slot; no unbounded request queue."""

    def __init__(self, config: CatalogueConfig, *, deadline: float | None = None):
        self.alias = config.alias
        self.deadline = deadline
        self._lock = threading.Lock()
        self._config = config
        self.connection = self._connect()

    def _connect(self):
        config = self._config
        d = DuckLakeConnectionFactory(config, duckdb_config={
            "threads": "2", "memory_limit": "512MB", "max_temp_directory_size": "256MB",
        }).connect(read_only=True)
        try:
            d.execute(f"USE {_identifier(config.alias)}.{PUBLIC_SCHEMA}")
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
        return d

    @property
    def healthy(self) -> bool:
        return self.connection is not None

    def close(self):
        with self._lock:
            if self.connection is not None:
                self.connection.close()

    def prepare(self, payload: QueryRequest, *, limits: QueryLimits = QueryLimits(), evidence: PreparationEvidence | None = None) -> PreparedQuery:
        return self._run(payload, execute=False, limits=limits, evidence=evidence)

    def execute(self, payload: QueryRequest, *, limits: QueryLimits = QueryLimits(), evidence: PreparationEvidence | None = None) -> QueryResult:
        return self._run(payload, execute=True, limits=limits, evidence=evidence)

    def _run(self, payload: QueryRequest, *, execute: bool, limits: QueryLimits, evidence: PreparationEvidence | None):
        if not self._lock.acquire(blocking=False):
            raise BusyError("Query server is busy. Try again shortly.")
        _active_queries.inc()
        try:
            if self.connection is None:
                self.connection = self._connect()
            return self._run_admitted(payload, execute=execute, limits=limits, evidence=evidence)
        finally:
            _active_queries.dec()
            self._lock.release()

    def _run_admitted(self, payload: QueryRequest, *, execute: bool, limits: QueryLimits, evidence: PreparationEvidence | None):
        started = time.monotonic()
        query_id = str(uuid4())
        d = self.connection
        invalidated = False
        expired = threading.Event()
        def interrupt():
            expired.set()
            d.interrupt()
        duration = limits.max_duration_seconds if self.deadline is None else min(self.deadline, limits.max_duration_seconds)
        if evidence is not None:
            evidence.duckdb_version = duckdb.__version__
            evidence.compiler_version = COMPILER_VERSION
            evidence.effective_limits = dict(max_rows=limits.max_rows, max_duration_seconds=duration,
                                             max_result_bytes=limits.max_result_bytes)
        timer = threading.Timer(duration, interrupt)
        timer.start()
        from periplus.platform.telemetry import event
        event("query_submitted", operation_id=query_id, operation="exec" if execute else "prep")
        status = "failed"
        rows = []
        truncated = False
        try:
            executable = _bounded_query(payload.sql, max_rows=limits.max_rows)
            statement = _one_statement(payload.sql)
            diagnostics = []
            if any(join.args.get("kind") == "CROSS" for join in statement.find_all(exp.Join)):
                diagnostics.append(Diagnostic(severity="warning", code="cartesian_product", message="A Cartesian product can require substantial work."))
            if evidence is not None:
                evidence.diagnostics = [item.model_dump() for item in diagnostics]
            d.execute("BEGIN TRANSACTION")
            snapshot = int(d.execute("SELECT id FROM ducklake_current_snapshot(?)", [self.alias]).fetchone()[0])
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
            if evidence is not None and not isinstance(statement, exp.Show):
                evidence.plan = plan
                evidence.plan_truncated = any(item.code == "plan_truncated" for item in diagnostics)
                # Exact preview identity, not an operator-shape or regression claim.
                evidence.plan_fingerprint = None if evidence.plan_truncated else hashlib.sha256(
                    (COMPILER_VERSION + "\n" + duckdb.__version__ + "\n" + plan).encode()).hexdigest()
                evidence.diagnostics = [item.model_dump() for item in diagnostics]
            prepared = PreparedQuery(query_id=query_id, sql=payload.sql, parameters=payload.parameters, diagnostics=diagnostics, plan=plan)
            if expired.is_set():
                raise TimeoutError("Query time limit exceeded.")
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
                if len(rows) == limits.max_rows or size > limits.max_result_bytes:
                    truncated = True
                    break
                if expired.is_set():
                    raise TimeoutError("Query time limit exceeded.")
                rows.append(converted)
            if expired.is_set():
                raise TimeoutError("Query time limit exceeded.")
            status = "completed"
            return QueryResult(**prepared.model_dump(), columns=columns, types=types, rows=rows, truncated=truncated, source_snapshot=snapshot, elapsed_ms=(time.monotonic()-started)*1000)
        except (duckdb.FatalException, duckdb.InternalException):
            invalidated = True
            raise
        except duckdb.InterruptException as exc:
            raise TimeoutError("Query time limit exceeded.") from exc
        finally:
            timer.cancel()
            timer.join()
            try:
                d.execute("ROLLBACK")
            except duckdb.TransactionException:
                pass
            except duckdb.Error:
                # Cleanup must not hide the original failure or retain a poisoned handle.
                invalidated = True
                if status in {"completed", "prepared"}:
                    raise
            finally:
                if invalidated:
                    self.connection = None
                    try:
                        d.close()
                    except duckdb.Error:
                        pass
                    logger.warning("query_connection_discarded query_id=%s", query_id)
                event("query_finished", operation_id=query_id, outcome=status, truncated=truncated, rows=len(rows), elapsed_ms=(time.monotonic()-started)*1000)


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
