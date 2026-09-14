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
from collections.abc import Callable
from periplus.platform.catalogue.public import PUBLIC_SCHEMA, EXPERIMENTAL_SCHEMA

import duckdb
from prometheus_client import Gauge

from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.connection import (
    DuckLakeConnectionFactory,
    _identifier,
)
from periplus.query.compiler import compile_query
from periplus.query.optimizations import passes_for_mode
from periplus.query.optimizations.base import OptimizationPass
from periplus.query.models import (
    COMPILER_VERSION,
    QueryMode,
    QueryRequest,
    PreparedQuery,
    QueryResult,
)
from periplus.operations.access.schemas import QueryLimits
from periplus.operations.query_history.schemas import PreparationEvidence

logger = logging.getLogger(__name__)
_active_queries = Gauge(
    "periplus_query_active_operations", "Occupied query admission slots."
)


class BusyError(Exception):
    pass


class ResultLimitError(Exception):
    pass


class QueryService:
    """One connection and admission slot; no unbounded request queue."""

    def __init__(
        self,
        config: CatalogueConfig,
        *,
        deadline: float | None = None,
        mode: QueryMode = QueryMode.STABLE,
        passes: tuple[OptimizationPass, ...] | None = None,
    ):
        self.mode = QueryMode(mode)
        self.passes = passes_for_mode(self.mode) if passes is None else passes
        self.schema = (
            EXPERIMENTAL_SCHEMA
            if self.mode == QueryMode.EXPERIMENTAL
            else PUBLIC_SCHEMA
        )
        self.compiler_version = f"{COMPILER_VERSION}:{self.mode.value}"
        self.alias = config.alias
        self.deadline = deadline
        self._lock = threading.Lock()
        self._config = config
        self.connection = self._connect()

    def _connect(self):
        config = self._config
        d = DuckLakeConnectionFactory(
            config,
            duckdb_config={
                "threads": "2",
                "memory_limit": "512MB",
                "max_temp_directory_size": "256MB",
            },
        ).connect(read_only=True)
        try:
            d.execute(f"USE {_identifier(config.alias)}.{self.schema}")
            # Extensions and credentials are installed before locking the session.
            # Only lake data paths may perform filesystem IO after this point.
            root = (
                config.data_path
                if "://" in config.data_path
                else str(Path(config.data_path).resolve())
            )
            d.execute("SET allowed_directories = ?", [[root.rstrip("/") + "/"]])
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

    def prepare(
        self,
        payload: QueryRequest,
        *,
        limits: QueryLimits = QueryLimits(),
        evidence: PreparationEvidence | None = None,
    ) -> PreparedQuery:
        return self._run(payload, execute=False, limits=limits, evidence=evidence)

    def execute(
        self,
        payload: QueryRequest,
        *,
        limits: QueryLimits = QueryLimits(),
        evidence: PreparationEvidence | None = None,
        emit: Callable[[dict], None] | None = None,
        cancelled: threading.Event | None = None,
    ) -> QueryResult:
        return self._run(
            payload,
            execute=True,
            limits=limits,
            evidence=evidence,
            emit=emit,
            cancelled=cancelled,
        )

    def _run(
        self,
        payload: QueryRequest,
        *,
        execute: bool,
        limits: QueryLimits,
        evidence: PreparationEvidence | None,
        emit=None,
        cancelled=None,
    ):
        if not self._lock.acquire(blocking=False):
            raise BusyError("Query server is busy. Try again shortly.")
        _active_queries.inc()
        try:
            if self.connection is None:
                self.connection = self._connect()
            return self._run_admitted(
                payload,
                execute=execute,
                limits=limits,
                evidence=evidence,
                emit=emit,
                cancelled=cancelled,
            )
        finally:
            _active_queries.dec()
            self._lock.release()

    def _run_admitted(
        self,
        payload: QueryRequest,
        *,
        execute: bool,
        limits: QueryLimits,
        evidence: PreparationEvidence | None,
        emit=None,
        cancelled=None,
    ):
        started = time.monotonic()
        query_id = str(uuid4())
        d = self.connection
        invalidated = False
        expired = threading.Event()

        def interrupt():
            expired.set()
            d.interrupt()

        duration = (
            limits.max_duration_seconds
            if self.deadline is None
            else min(self.deadline, limits.max_duration_seconds)
        )
        if evidence is not None:
            evidence.duckdb_version = duckdb.__version__
            evidence.compiler_version = self.compiler_version
            evidence.effective_limits = limits.model_dump() | {
                "max_duration_seconds": duration
            }
        timer = threading.Timer(duration, interrupt)
        timer.start()
        from periplus.platform.telemetry import event

        event(
            "query_submitted",
            operation_id=query_id,
            operation="exec" if execute else "prep",
        )
        status = "failed"
        rows = []
        truncated = False
        row_count = 0
        try:
            if (
                payload.schema_version is not None
                and payload.schema_version != self.schema
            ):
                raise ValueError(
                    f"This endpoint serves {self.schema}; use its matching schema or omit schema_version."
                )
            d.execute("BEGIN TRANSACTION")
            snapshot = int(
                d.execute(
                    "SELECT id FROM ducklake_current_snapshot(?)", [self.alias]
                ).fetchone()[0]
            )
            compiled = compile_query(
                d,
                payload,
                schema=self.schema,
                catalogue_alias=self.alias,
                execute=execute,
                max_rows=limits.max_rows,
                passes=self.passes,
            )
            executable = compiled.executable_sql
            execution_parameters = payload.parameters
            prepared = PreparedQuery(
                schema_version=self.schema,
                optimizations=compiled.optimizations,
                query_mode=self.mode,
                compiler_version=self.compiler_version,
                query_id=query_id,
                sql=payload.sql,
                parameters=payload.parameters,
                diagnostics=compiled.diagnostics,
                plan=compiled.plan,
            )
            if evidence is not None and compiled.record_plan:
                evidence.plan = compiled.plan
                evidence.plan_truncated = compiled.plan_truncated
                evidence.plan_fingerprint = (
                    None
                    if compiled.plan_truncated
                    else hashlib.sha256(
                        (
                            self.compiler_version
                            + "\n"
                            + duckdb.__version__
                            + "\n"
                            + compiled.plan
                        ).encode()
                    ).hexdigest()
                )
                evidence.diagnostics = [
                    item.model_dump() for item in compiled.diagnostics
                ]
            if expired.is_set():
                raise TimeoutError("Query time limit exceeded.")
            if not execute:
                status = "prepared"
                return prepared
            cursor = d.execute(executable, execution_parameters)
            columns = [str(col[0]) for col in cursor.description]
            types = [str(col[1]) for col in cursor.description]
            rows = []
            size = (
                len(prepared.model_dump_json().encode())
                + len(json.dumps([columns, types]).encode())
                + 1024
            )
            if size > limits.max_result_bytes:
                raise ResultLimitError(
                    f"Query metadata exceeds max_result_bytes ({limits.max_result_bytes} bytes)."
                )
            truncated = False
            reason = None
            batch_bytes = 0
            row_payload_bytes = 2
            if emit:
                emit(
                    dict(
                        type="metadata",
                        **prepared.model_dump(mode="json"),
                        columns=columns,
                        types=types,
                        source_snapshot=snapshot,
                        limits=limits.model_dump(),
                    )
                )
            while True:
                if cancelled is not None and cancelled.is_set():
                    raise TimeoutError("Query stream was cancelled.")
                if expired.is_set():
                    raise TimeoutError("Query time limit exceeded.")
                row = cursor.fetchone()
                if row is None:
                    break
                converted = [_json_value(value) for value in row]
                row_bytes = len(json.dumps(converted, ensure_ascii=False).encode()) + 1
                if (
                    row_count == limits.max_rows
                    or size + row_bytes > limits.max_result_bytes
                ):
                    truncated = True
                    reason = (
                        "max_rows"
                        if row_count == limits.max_rows
                        else "max_result_bytes"
                    )
                    break
                size += row_bytes
                row_payload_bytes += len(
                    json.dumps(
                        converted, ensure_ascii=False, separators=(",", ":")
                    ).encode()
                ) + (1 if row_count else 0)
                row_count += 1
                rows.append(converted)
                batch_bytes += row_bytes
                if emit and (len(rows) >= 1024 or batch_bytes >= 256 * 1024):
                    emit(dict(type="rows", rows=rows))
                    rows = []
                    batch_bytes = 0
            if expired.is_set():
                raise TimeoutError("Query time limit exceeded.")
            if emit and rows:
                emit(dict(type="rows", rows=rows))
                rows = []
            status = "completed"
            return QueryResult(
                **prepared.model_dump(),
                columns=columns,
                types=types,
                rows=rows,
                row_count=row_count,
                result_bytes=row_payload_bytes,
                truncated=truncated,
                truncation_reason=reason,
                source_snapshot=snapshot,
                elapsed_ms=(time.monotonic() - started) * 1000,
            )

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
                event(
                    "query_finished",
                    operation_id=query_id,
                    outcome=status,
                    truncated=truncated,
                    rows=row_count,
                    elapsed_ms=(time.monotonic() - started) * 1000,
                )


def _json_value(value):
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) > 2**53 - 1
    ):
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
