"""Bounded native ClickHouse queries under public-view-only database credentials."""

import hashlib
import json
import threading
import time
from uuid import uuid4

from pydantic import JsonValue
from prometheus_client import Gauge
import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType

from periplus.operations.access.schemas import QueryLimits
from periplus.operations.query_history.schemas import PreparationEvidence
from periplus.platform.clickhouse import ClickHouseClient, ClickHouseConfig
from periplus.platform.clickhouse.public import PUBLIC_RELATIONS
from periplus.query.binding import PublicationBinding, bind_publication
from periplus.query.validation import QueryClickHouse
from periplus.query.models import PreparedQuery, QueryMode, QueryRequest, QueryResult


_active_queries = Gauge(
    "periplus_query_active_operations", "Occupied query admission slots."
)


class BusyError(Exception):
    pass


class ResultLimitError(Exception):
    pass


def public_sql(payload: QueryRequest) -> str:
    if payload.schema_version not in {None, "public_v1"}:
        raise ValueError("The ClickHouse catalogue currently exposes public_v1.")
    try:
        tokens = QueryClickHouse().tokenize(payload.sql)
        placeholders = [
            token for token in tokens if token.token_type == TokenType.PLACEHOLDER
        ]
        if len(placeholders) != len(payload.parameters):
            raise ValueError("Positional parameter count does not match the query.")
        json.dumps(payload.parameters, allow_nan=False)
        sql = payload.sql
        for token, value in reversed(
            list(zip(placeholders, payload.parameters, strict=True))
        ):
            literal = exp.convert(value).sql(dialect="clickhouse")
            sql = sql[: token.start] + literal + sql[token.end + 1 :]
        statements = sqlglot.parse(
            sql, read="clickhouse", error_level=sqlglot.ErrorLevel.RAISE
        )
    except sqlglot.errors.ParseError:
        raise ValueError("SQL could not be parsed as ClickHouse SQL.") from None
    if len(statements) != 1 or not isinstance(
        statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)
    ):
        raise ValueError("Submit one read-only SELECT query.")
    tree = statements[0]
    if any(
        node.args.get("settings")
        or node.args.get("format")
        or isinstance(node, exp.Into)
        for node in tree.walk()
    ):
        raise ValueError(
            "Query settings, output destinations and formats are service-owned."
        )
    from sqlglot.optimizer.scope import Scope, traverse_scope

    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier) or table.catalog:
            raise ValueError("External sources and table functions are unavailable.")
    # Resolve CTEs in their lexical scope. A CTE in one UNION branch must not
    # prevent a public table in another branch from receiving its binding.
    for scope in traverse_scope(tree):
        for table in scope.tables:
            if not table.db and isinstance(
                scope.sources.get(table.alias_or_name), Scope
            ):
                continue
            if table.db not in {"", "public_v1"} or table.name not in PUBLIC_RELATIONS:
                raise ValueError(
                    "Queries may reference only the installed public_v1 relations."
                )
            table.set("db", exp.to_identifier("public_v1"))
    if any(tree.find_all(exp.Placeholder, exp.Parameter)):
        raise ValueError("Only anonymous positional parameters are supported.")
    return tree.sql(dialect="clickhouse", comments=False)


def wire_value(value: JsonValue) -> JsonValue:
    """Preserve integer precision in JSON consumed by JavaScript clients."""
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) > 2**53 - 1
    ):
        return str(value)
    if isinstance(value, list):
        return [wire_value(item) for item in value]
    if isinstance(value, dict):
        return {key: wire_value(item) for key, item in value.items()}
    return value


class QueryService:
    def __init__(
        self,
        config: ClickHouseConfig,
        *,
        deadline: float | None = None,
        mode: QueryMode = QueryMode.STABLE,
    ):
        if mode != QueryMode.STABLE or not config.query_only:
            raise ValueError(
                "Query service requires public_v1 and query-only credentials."
            )
        self.mode = mode
        self.schema = "public_v1"
        self.compiler_version = "clickhouse-public-v1"
        self.deadline = deadline
        self.client = ClickHouseClient(config)
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._closed = False
        try:
            self.engine_version = self.client.execute("SELECT version()", max_response_bytes=80).decode().strip()
        except BaseException:
            self.client.close()
            raise

    @property
    def healthy(self) -> bool:
        return not self._closed

    def close(self) -> None:
        with self._lock:
            self.client.close()
            self._closed = True

    def interrupt(self) -> None:
        self._cancelled.set()
        self.client.interrupt_query()

    def prepare(
        self,
        payload: QueryRequest,
        *,
        limits: QueryLimits = QueryLimits(),
        evidence: PreparationEvidence | None = None,
        publication: PublicationBinding | None = None,
    ) -> PreparedQuery:
        return self._run(
            payload, limits, evidence, execute=False, publication=publication
        )

    def execute(
        self,
        payload: QueryRequest,
        *,
        limits: QueryLimits = QueryLimits(),
        evidence: PreparationEvidence | None = None,
        emit=None,
        cancelled=None,
        publication: PublicationBinding | None = None,
    ) -> QueryResult:
        return self._run(
            payload,
            limits,
            evidence,
            execute=True,
            emit=emit,
            cancelled=cancelled,
            publication=publication,
        )

    def _run(
        self,
        payload,
        limits,
        evidence,
        *,
        execute,
        emit=None,
        cancelled=None,
        publication=None,
    ):
        if not self._lock.acquire(blocking=False):
            raise BusyError("Query server is busy.")
        _active_queries.inc()
        self._cancelled.clear()
        started = time.monotonic()
        duration = min(45, limits.max_duration_seconds, self.deadline or 45)
        query_id = str(uuid4())

        def remaining():
            value = duration - (time.monotonic() - started)
            if (
                value <= 0
                or self._cancelled.is_set()
                or (cancelled is not None and cancelled.is_set())
            ):
                raise TimeoutError(
                    "Query execution was cancelled or exceeded its deadline."
                )
            return value

        try:
            if self._closed:
                raise RuntimeError("Query service is closed.")
            sql = public_sql(payload)
            if publication is not None:
                sql = bind_publication(sql, publication)
            plan = self.client.execute(
                "EXPLAIN PLAN " + sql,
                query_id=query_id + "-plan",
                cancelled=self._cancelled,
                timeout_seconds=remaining(),
                max_response_bytes=64000,
            ).decode()
            prepared = PreparedQuery(
                query_mode=self.mode,
                compiler_version=self.compiler_version,
                schema_version=self.schema,
                query_id=query_id,
                sql=payload.sql,
                parameters=payload.parameters,
                diagnostics=[],
                plan=plan,
            )
            if evidence is not None:
                evidence.compiler_version = self.compiler_version
                evidence.engine_version = self.engine_version
                evidence.plan = plan
                evidence.plan_truncated = False
                evidence.plan_fingerprint = hashlib.sha256(plan.encode()).hexdigest()
                evidence.diagnostics = []
                evidence.effective_limits = limits.model_dump() | {
                    "max_duration_seconds": duration
                }
            if not execute:
                remaining()
                return prepared
            # The outer limit bounds delivery while preserving the submitted query
            # as a subquery, including its ordering and aggregate semantics.
            body = self.client.execute(
                f"SELECT * FROM ({sql}) LIMIT {limits.max_rows + 1} FORMAT JSONCompact",
                query_id=query_id,
                cancelled=self._cancelled,
                timeout_seconds=remaining(),
                max_response_bytes=min(
                    16 * 1024 * 1024, limits.max_result_bytes + 1024 * 1024
                ),
            )
            remaining()
            result = json.loads(body)
            columns = [item["name"] for item in result["meta"]]
            types = [item["type"] for item in result["meta"]]
            size = (
                len(prepared.model_dump_json().encode())
                + len(json.dumps([columns, types]).encode())
                + 1024
            )
            if size > limits.max_result_bytes:
                raise ResultLimitError("Query metadata exceeds the result byte budget.")
            rows = []
            reason = None
            for native_row in result["data"]:
                row = [wire_value(value) for value in native_row]
                if len(rows) == limits.max_rows:
                    reason = "max_rows"
                    break
                count = (
                    len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode())
                    + 1
                )
                if size + count > limits.max_result_bytes:
                    reason = "max_result_bytes"
                    break
                rows.append(row)
                size += count
            answer = QueryResult(
                **prepared.model_dump(),
                columns=columns,
                types=types,
                rows=rows,
                truncated=reason is not None,
                truncation_reason=reason,
                source_snapshot=None,
                elapsed_ms=(time.monotonic() - started) * 1000,
                row_count=len(rows),
                result_bytes=size,
            )
            if emit:
                emit(
                    dict(
                        type="metadata",
                        **prepared.model_dump(mode="json"),
                        columns=columns,
                        types=types,
                        source_snapshot=None,
                        limits=limits.model_dump(),
                    )
                )
                for offset in range(0, len(rows), 128):
                    remaining()
                    emit(dict(type="rows", rows=rows[offset : offset + 128]))
            return answer
        finally:
            _active_queries.dec()
            self._lock.release()
