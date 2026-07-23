"""API-owned pooled Quack query runtime."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import time
from typing import TypeVar
from uuid import UUID, uuid4

import duckdb
import pyarrow as pa

from config import get_float, get_int, get_str
from observability import catalogue_query_metrics
from repository.catalogue.duckbasin import (
    DuckBasinClientMinter,
    MintedDuckDB,
)
from repository.catalogue.query import CatalogueStatementKind
from repository.catalogue.schema import CATALOGUE_SCHEMA_VERSION
from runtime.catalogue_queries import (
    CatalogueQueryState,
    get_catalogue_query,
    update_catalogue_query,
)
from runtime.resource_governor import (
    ResourceCapacityUnavailable,
    ResourceNeed,
    ResourcePermitGuard,
    ResourceRequest,
    resource_permits,
)


T = TypeVar("T")
_QUERY_POLL_SECONDS = 0.25


class CatalogueQueryExecutionError(RuntimeError):
    """An interactive query could not be prepared or streamed."""


@dataclass(frozen=True, slots=True)
class QuackRuntimeConfig:
    catalogue_alias: str
    catalogue_schema: str
    catalogue_schema_version: str
    maximum_concurrency: int
    pool_wait_seconds: float
    query_timeout_seconds: float
    maximum_rows: int
    maximum_result_bytes: int

    @classmethod
    def from_env(cls) -> QuackRuntimeConfig:
        return cls(
            catalogue_alias=get_str("DUCKBASIN_LAKE"),
            catalogue_schema=get_str("ATLAS_CATALOGUE_SCHEMA"),
            catalogue_schema_version=CATALOGUE_SCHEMA_VERSION,
            maximum_concurrency=get_int("ATLAS_QUACK_MAX_CONCURRENCY"),
            pool_wait_seconds=get_float("ATLAS_QUACK_POOL_WAIT_SECONDS"),
            query_timeout_seconds=get_float("ATLAS_QUACK_QUERY_TIMEOUT_SECONDS"),
            maximum_rows=get_int("ATLAS_QUACK_QUERY_MAX_ROWS"),
            maximum_result_bytes=get_int("ATLAS_QUACK_QUERY_MAX_BYTES"),
        )


class _QuackSlot:
    def __init__(
        self,
        index: int,
        config: QuackRuntimeConfig,
        minter: DuckBasinClientMinter,
    ) -> None:
        self.index = index
        self.config = config
        self.minter = minter
        self.executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"atlas-quack-{index}",
        )
        self.minted: MintedDuckDB | None = None
        self.connection: duckdb.DuckDBPyConnection | None = None

    async def open(self) -> None:
        await self.submit(self._open)

    async def close(self) -> None:
        try:
            await self.submit(self._close)
        finally:
            self.executor.shutdown(wait=True, cancel_futures=True)

    async def submit(self, operation: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, operation)

    def interrupt(self) -> None:
        if self.connection is not None:
            self.connection.interrupt()

    def _open(self) -> None:
        if self.connection is not None:
            raise RuntimeError("Quack slot is already open")
        minted = self.minter.mint()
        self.minted = minted
        self.connection = minted.connection

    def _close(self) -> None:
        minted = self.minted
        if minted is None:
            return
        minted.close()
        self.minted = None
        self.connection = None


class _ChunkSink:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.position = 0
        self.closed = False

    def writable(self) -> bool:
        return not self.closed

    def write(self, data: bytes) -> int:
        chunk = bytes(data)
        self.chunks.append(chunk)
        self.position += len(chunk)
        return len(chunk)

    def tell(self) -> int:
        return self.position

    def drain(self) -> list[bytes]:
        chunks, self.chunks = self.chunks, []
        return chunks


class _BoundedArrowStream:
    def __init__(
        self,
        reader: pa.RecordBatchReader,
        *,
        maximum_rows: int,
        maximum_bytes: int,
    ) -> None:
        self.reader = reader
        self.maximum_rows = maximum_rows
        self.maximum_bytes = maximum_bytes
        self.row_count = 0
        self.result_bytes = 0
        self.limit_error: str | None = None
        self._sink = _ChunkSink()
        self._writer = pa.ipc.new_stream(self._sink, reader.schema)
        self._pending = deque(self._sink.drain())
        self._closed = False

    def next_chunk(self) -> bytes | None:
        while not self._pending and not self._closed:
            try:
                batch = next(self.reader)
            except StopIteration:
                self._close_writer()
                break
            if self.row_count + batch.num_rows > self.maximum_rows:
                self.limit_error = (
                    f"query result exceeded {self.maximum_rows:,} rows"
                )
                self._close_writer()
                break
            self._writer.write_batch(batch)
            chunks = self._sink.drain()
            if self.result_bytes + sum(map(len, chunks)) > self.maximum_bytes:
                self.limit_error = (
                    f"query result exceeded {self.maximum_bytes:,} encoded bytes"
                )
                self._close_writer()
                break
            self.row_count += batch.num_rows
            self._pending.extend(chunks)
        if not self._pending:
            return None
        chunk = self._pending.popleft()
        self.result_bytes += len(chunk)
        return chunk

    def _close_writer(self) -> None:
        if self._closed:
            return
        self._writer.close()
        chunks = self._sink.drain()
        remaining = self.maximum_bytes - self.result_bytes
        for chunk in chunks:
            if len(chunk) <= remaining:
                self._pending.append(chunk)
                remaining -= len(chunk)
            else:
                self.limit_error = (
                    self.limit_error
                    or f"query result exceeded {self.maximum_bytes:,} encoded bytes"
                )
                break
        self._closed = True


class ActiveCatalogueQuery:
    def __init__(
        self,
        runtime: QuackQueryRuntime,
        *,
        query_id: UUID,
        statement_kind: CatalogueStatementKind,
        slot: _QuackSlot,
        arrow: _BoundedArrowStream,
        permit_context,
        permit_guard: ResourcePermitGuard,
        monitor: asyncio.Task,
        started_monotonic: float,
    ) -> None:
        self.runtime = runtime
        self.query_id = query_id
        self.statement_kind = statement_kind
        self.slot = slot
        self.arrow = arrow
        self.permit_context = permit_context
        self.permit_guard = permit_guard
        self.monitor = monitor
        self.started_monotonic = started_monotonic
        self.closed = False

    async def stream(self) -> AsyncIterator[bytes]:
        outcome = "succeeded"
        error: str | None = None
        try:
            chunk = await self.slot.submit(self.arrow.next_chunk)
            while chunk is not None:
                next_chunk = await self.slot.submit(self.arrow.next_chunk)
                await self.runtime._record_progress(
                    self.query_id,
                    rows=self.arrow.row_count,
                    result_bytes=self.arrow.result_bytes,
                )
                if next_chunk is None:
                    reason = self.runtime._cancel_reasons.get(self.query_id)
                    if reason is not None:
                        outcome = "cancelled"
                        error = reason
                    elif self.arrow.limit_error is not None:
                        outcome = "failed"
                        error = self.arrow.limit_error
                    await self.close(outcome=outcome, error=error)
                    yield chunk
                    return
                yield chunk
                chunk = next_chunk
        except asyncio.CancelledError:
            outcome = "cancelled"
            error = "client disconnected"
            self.runtime.interrupt_local(self.query_id, error)
            raise
        except GeneratorExit:
            outcome = "cancelled"
            error = "client disconnected"
            self.runtime.interrupt_local(self.query_id, error)
            raise
        except duckdb.InterruptException:
            outcome = "cancelled"
            error = self.runtime._cancel_reasons.get(
                self.query_id, "query was interrupted"
            )
        except Exception as exc:
            reason = self.runtime._cancel_reasons.get(self.query_id)
            outcome = "cancelled" if reason is not None else "failed"
            error = reason or self.runtime.safe_error(exc)
        finally:
            await self.close(outcome=outcome, error=error)

    async def close(self, *, outcome: str, error: str | None) -> None:
        if self.closed:
            return
        self.closed = True
        if outcome != "succeeded":
            self.slot.interrupt()
        await self.slot.submit(lambda: None)
        self.monitor.cancel()
        await asyncio.gather(self.monitor, return_exceptions=True)
        await self.runtime._finish_query(
            self,
            outcome=outcome,
            error=error,
        )


class QuackQueryRuntime:
    def __init__(
        self,
        query_bucket,
        resource_bucket,
        *,
        config: QuackRuntimeConfig | None = None,
        minter: DuckBasinClientMinter | None = None,
    ) -> None:
        self.config = config or QuackRuntimeConfig.from_env()
        self.query_bucket = query_bucket
        self.resource_bucket = resource_bucket
        self._minter = minter
        self._owns_minter = minter is None
        self._slots: list[_QuackSlot] = []
        self._available: asyncio.Queue[_QuackSlot] = asyncio.Queue()
        self._active: dict[UUID, _QuackSlot] = {}
        self._cancel_reasons: dict[UUID, str] = {}

    async def start(self) -> None:
        try:
            if self._minter is None:
                self._minter = DuckBasinClientMinter()
            for index in range(self.config.maximum_concurrency):
                slot = _QuackSlot(index, self.config, self._minter)
                await slot.open()
                self._slots.append(slot)
            for slot in self._slots:
                await slot.submit(
                    lambda slot=slot: self._connection(slot).execute(
                        "USE "
                        f"{_quote_identifier(self.config.catalogue_alias)}."
                        f"{_quote_identifier(self.config.catalogue_schema)}",
                    )
                )
                self._available.put_nowait(slot)
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        for slot in self._active.values():
            slot.interrupt()
        self._active.clear()
        await asyncio.gather(
            *(slot.close() for slot in self._slots),
            return_exceptions=True,
        )
        self._slots.clear()
        if self._owns_minter and self._minter is not None:
            await asyncio.to_thread(self._minter.close)
            self._minter = None
        while not self._available.empty():
            self._available.get_nowait()

    async def prepare(
        self,
        *,
        query_id: UUID,
        sql: str,
        statement_kind: CatalogueStatementKind,
    ) -> ActiveCatalogueQuery:
        request = ResourceRequest(
            operation_id=f"catalogue-query:{query_id.hex}",
            service_class="live",
            resources=(
                ResourceNeed(
                    name="quack:query",
                    units=1,
                    capacity=self.config.maximum_concurrency,
                ),
            ),
        )
        permit_context = resource_permits(
            self.resource_bucket,
            request,
            acquire_timeout=self.config.pool_wait_seconds,
        )
        try:
            permit_guard = await permit_context.__aenter__()
        except ResourceCapacityUnavailable as exc:
            await self._record_terminal(
                query_id,
                outcome="failed",
                error="interactive query capacity is busy",
            )
            raise CatalogueQueryExecutionError(
                "Interactive query capacity is busy; retry shortly."
            ) from exc
        try:
            slot = await asyncio.wait_for(
                self._available.get(),
                timeout=self.config.pool_wait_seconds,
            )
        except asyncio.CancelledError:
            await permit_context.__aexit__(None, None, None)
            await self._record_terminal(
                query_id,
                outcome="cancelled",
                error="client disconnected",
            )
            raise
        except TimeoutError as exc:
            await permit_context.__aexit__(None, None, None)
            await self._record_terminal(
                query_id,
                outcome="failed",
                error="local query pool is busy",
            )
            raise CatalogueQueryExecutionError(
                "Interactive query capacity is busy; retry shortly."
            ) from exc

        self._active[query_id] = slot
        started_monotonic = time.monotonic()
        try:
            state = await get_catalogue_query(self.query_bucket, query_id)
        except Exception as exc:
            active = self._empty_active_query(
                query_id=query_id,
                statement_kind=statement_kind,
                slot=slot,
                permit_context=permit_context,
                permit_guard=permit_guard,
                started_monotonic=started_monotonic,
            )
            await active.close(
                outcome="failed",
                error="query state is unavailable",
            )
            raise CatalogueQueryExecutionError(
                "Catalogue query state is unavailable; retry shortly."
            ) from exc
        if state is None or state.cancel_requested_at is not None:
            reason = (
                "query state expired"
                if state is None
                else "query cancellation was requested"
            )
            self._cancel_reasons[query_id] = reason
            active = self._empty_active_query(
                query_id=query_id,
                statement_kind=statement_kind,
                slot=slot,
                permit_context=permit_context,
                permit_guard=permit_guard,
                started_monotonic=started_monotonic,
            )
            await active.close(outcome="cancelled", error=reason)
            raise CatalogueQueryExecutionError(reason)
        try:
            await update_catalogue_query(
                self.query_bucket,
                query_id,
                lambda state: state.model_copy(
                    update={"status": "running", "started_at": datetime.now(UTC)}
                ),
            )
        except Exception as exc:
            active = self._empty_active_query(
                query_id=query_id,
                statement_kind=statement_kind,
                slot=slot,
                permit_context=permit_context,
                permit_guard=permit_guard,
                started_monotonic=started_monotonic,
            )
            await active.close(
                outcome="failed",
                error="query state is unavailable",
            )
            raise CatalogueQueryExecutionError(
                "Catalogue query state is unavailable; retry shortly."
            ) from exc
        monitor = asyncio.create_task(
            self._monitor(query_id, permit_guard, started_monotonic),
            name=f"catalogue-query-monitor:{query_id.hex}",
        )
        executable_sql = (
            sql
            if statement_kind == CatalogueStatementKind.QUERY
            else (
                "EXPLAIN ("
                + (
                    "ANALYZE, FORMAT JSON"
                    if statement_kind == CatalogueStatementKind.EXPLAIN_ANALYZE
                    else "FORMAT JSON"
                )
                + f") {sql}"
            )
        )
        try:
            arrow = await slot.submit(
                lambda: _start_arrow_stream(
                    self._connection(slot),
                    executable_sql,
                    maximum_rows=self.config.maximum_rows,
                    maximum_bytes=self.config.maximum_result_bytes,
                )
            )
        except asyncio.CancelledError:
            reason = "client disconnected"
            self.interrupt_local(query_id, reason)
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            active = self._empty_active_query(
                query_id=query_id,
                statement_kind=statement_kind,
                slot=slot,
                permit_context=permit_context,
                permit_guard=permit_guard,
                started_monotonic=started_monotonic,
            )
            await active.close(outcome="cancelled", error=reason)
            raise
        except Exception as exc:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            reason = self._cancel_reasons.get(query_id)
            active = self._empty_active_query(
                query_id=query_id,
                statement_kind=statement_kind,
                slot=slot,
                permit_context=permit_context,
                permit_guard=permit_guard,
                started_monotonic=started_monotonic,
            )
            await active.close(
                outcome="cancelled" if reason is not None else "failed",
                error=reason or self.safe_error(exc),
            )
            raise CatalogueQueryExecutionError(
                reason or self.safe_error(exc)
            ) from exc
        return ActiveCatalogueQuery(
            self,
            query_id=query_id,
            statement_kind=statement_kind,
            slot=slot,
            arrow=arrow,
            permit_context=permit_context,
            permit_guard=permit_guard,
            monitor=monitor,
            started_monotonic=started_monotonic,
        )

    async def run_internal(self, operation: Callable[[duckdb.DuckDBPyConnection], T]) -> T:
        operation_id = f"catalogue-internal:{uuid4().hex}"
        request = ResourceRequest(
            operation_id=operation_id,
            service_class="live",
            resources=(
                ResourceNeed(
                    name="quack:query",
                    units=1,
                    capacity=self.config.maximum_concurrency,
                ),
            ),
        )
        try:
            async with resource_permits(
                self.resource_bucket,
                request,
                acquire_timeout=self.config.pool_wait_seconds,
            ):
                try:
                    slot = await asyncio.wait_for(
                        self._available.get(),
                        timeout=self.config.pool_wait_seconds,
                    )
                except TimeoutError as exc:
                    raise CatalogueQueryExecutionError(
                        "Interactive query capacity is busy; retry shortly."
                    ) from exc
                try:
                    return await asyncio.wait_for(
                        slot.submit(lambda: operation(self._connection(slot))),
                        timeout=self.config.query_timeout_seconds,
                    )
                except asyncio.CancelledError:
                    slot.interrupt()
                    await slot.submit(lambda: None)
                    raise
                except TimeoutError as exc:
                    slot.interrupt()
                    await slot.submit(lambda: None)
                    raise CatalogueQueryExecutionError(
                        "Catalogue metadata query timed out."
                    ) from exc
                except Exception as exc:
                    raise CatalogueQueryExecutionError(
                        self.safe_error(exc)
                    ) from exc
                finally:
                    self._available.put_nowait(slot)
        except ResourceCapacityUnavailable as exc:
            raise CatalogueQueryExecutionError(
                "Interactive query capacity is busy; retry shortly."
            ) from exc

    def interrupt_local(self, query_id: UUID, reason: str) -> None:
        self._cancel_reasons.setdefault(query_id, reason)
        slot = self._active.get(query_id)
        if slot is not None:
            slot.interrupt()

    def _empty_active_query(
        self,
        *,
        query_id: UUID,
        statement_kind: CatalogueStatementKind,
        slot: _QuackSlot,
        permit_context,
        permit_guard: ResourcePermitGuard,
        started_monotonic: float,
    ) -> ActiveCatalogueQuery:
        return ActiveCatalogueQuery(
            self,
            query_id=query_id,
            statement_kind=statement_kind,
            slot=slot,
            arrow=_empty_arrow_stream(
                self.config.maximum_rows,
                self.config.maximum_result_bytes,
            ),
            permit_context=permit_context,
            permit_guard=permit_guard,
            monitor=asyncio.create_task(asyncio.sleep(0)),
            started_monotonic=started_monotonic,
        )

    def safe_error(self, error: BaseException) -> str:
        message = str(error)
        for slot in self._slots:
            if slot.minted is not None:
                message = message.replace(slot.minted.quack_uri, "[redacted]")
        return message[:2_000] or error.__class__.__name__

    async def _monitor(
        self,
        query_id: UUID,
        permit_guard: ResourcePermitGuard,
        started_monotonic: float,
    ) -> None:
        while query_id in self._active:
            if permit_guard.lost:
                self.interrupt_local(query_id, "global query permit was lost")
                return
            if (
                time.monotonic() - started_monotonic
                >= self.config.query_timeout_seconds
            ):
                self.interrupt_local(
                    query_id,
                    f"query exceeded {self.config.query_timeout_seconds:g}s timeout",
                )
                return
            try:
                state = await get_catalogue_query(self.query_bucket, query_id)
            except Exception:
                logging.warning(
                    "catalogue query state read unavailable",
                    extra={"query_id": query_id.hex},
                    exc_info=True,
                )
                await asyncio.sleep(_QUERY_POLL_SECONDS)
                continue
            if state is None:
                self.interrupt_local(query_id, "query state expired")
                return
            if state.cancel_requested_at is not None:
                self.interrupt_local(query_id, "query cancellation was requested")
                return
            await asyncio.sleep(_QUERY_POLL_SECONDS)

    async def _record_progress(
        self,
        query_id: UUID,
        *,
        rows: int,
        result_bytes: int,
    ) -> None:
        try:
            await update_catalogue_query(
                self.query_bucket,
                query_id,
                lambda state: state.model_copy(
                    update={"row_count": rows, "result_bytes": result_bytes}
                ),
            )
        except Exception:
            logging.warning(
                "catalogue query progress update unavailable",
                extra={"query_id": query_id.hex},
                exc_info=True,
            )

    async def _record_terminal(
        self,
        query_id: UUID,
        *,
        outcome: str,
        error: str | None,
    ) -> None:
        status = {
            "succeeded": "succeeded",
            "cancelled": "cancelled",
        }.get(outcome, "failed")
        await update_catalogue_query(
            self.query_bucket,
            query_id,
            lambda state: state.model_copy(
                update={
                    "status": status,
                    "completed_at": datetime.now(UTC),
                    "error": error,
                }
            ),
        )

    async def _finish_query(
        self,
        active: ActiveCatalogueQuery,
        *,
        outcome: str,
        error: str | None,
    ) -> None:
        self._active.pop(active.query_id, None)
        try:
            await self._record_progress(
                active.query_id,
                rows=active.arrow.row_count,
                result_bytes=active.arrow.result_bytes,
            )
            await self._record_terminal(
                active.query_id,
                outcome=outcome,
                error=error,
            )
        finally:
            self._available.put_nowait(active.slot)
            await active.permit_context.__aexit__(None, None, None)
            duration = time.monotonic() - active.started_monotonic
            catalogue_query_metrics.completed(
                statement_kind=active.statement_kind.value,
                outcome=outcome,
                duration_seconds=duration,
                rows=active.arrow.row_count,
                result_bytes=active.arrow.result_bytes,
            )
            logging.info(
                "catalogue query completed",
                extra={
                    "query_id": active.query_id.hex,
                    "statement_kind": active.statement_kind.value,
                    "outcome": outcome,
                    "duration_seconds": duration,
                    "row_count": active.arrow.row_count,
                    "result_bytes": active.arrow.result_bytes,
                },
            )
            self._cancel_reasons.pop(active.query_id, None)

    @staticmethod
    def _connection(slot: _QuackSlot) -> duckdb.DuckDBPyConnection:
        if slot.connection is None:
            raise RuntimeError("Quack slot is not open")
        return slot.connection


def remote_rows(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
) -> list[tuple]:
    return _remote_rows(connection, sql)


def _remote_rows(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
) -> list[tuple]:
    return connection.execute(
        "FROM quack_query_by_name(current_catalog(), ?)",
        [sql],
    ).fetchall()


def _start_arrow_stream(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    *,
    maximum_rows: int,
    maximum_bytes: int,
) -> _BoundedArrowStream:
    cursor = connection.execute(
        "FROM quack_query_by_name(current_catalog(), ?)",
        [sql],
    )
    return _BoundedArrowStream(
        cursor.to_arrow_reader(batch_size=65_536),
        maximum_rows=maximum_rows,
        maximum_bytes=maximum_bytes,
    )


def _empty_arrow_stream(
    maximum_rows: int,
    maximum_bytes: int,
) -> _BoundedArrowStream:
    schema = pa.schema([])
    return _BoundedArrowStream(
        pa.RecordBatchReader.from_batches(schema, []),
        maximum_rows=maximum_rows,
        maximum_bytes=maximum_bytes,
    )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
