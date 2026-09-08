"""Durable CDC ownership for active-generation materialization."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID, uuid5

import duckdb

from periplus.materialization.contracts import LiveBatchWork
from periplus.materialization import state
from periplus.materialization.state import ActiveGeneration
from periplus.retention.identities import write_claims
from periplus.materialization.registry import REGISTRY_DIGEST
from periplus.materialization.sql import sql_string, sql_string_list
from periplus.platform.catalogue import catalogue_from_env
from periplus.platform.messaging.leases import (
    OperationLeaseGuard,
    OperationLeaseLost,
    OperationLeaseUnavailable,
    ensure_operation_lease_storage,
    operation_leases,
)

_CDC_STATE_SCHEMA = "periplus_ducklake_cdc"
_CONSUMER_PREFIX = "periplus_live_visits_"
_LEADER_PHASE = "materialization-cdc"
_LEADER_IDENTITY = "ingest.visits"
_WINDOW_SNAPSHOTS = 100
_LISTEN_TIMEOUT_MS = 1_000
_HEARTBEAT_SECONDS = 5.0


class RegistryMismatch(RuntimeError):
    """The active material generation belongs to another deployment."""


@dataclass(frozen=True, slots=True)
class ChangeWindow:
    start_snapshot: int
    end_snapshot: int
    visit_ids: tuple[str, ...]


class LiveCdcConnection:
    """One dedicated DuckDB connection holding the CDC consumer lease."""

    def __init__(self) -> None:
        self.catalogue = catalogue_from_env(
            threads=1,
            memory_limit="512MB",
            load_cdc=True,
        )
        connection = self.catalogue.trusted_connection
        connection.execute(
            "CALL cdc_configure("
            f"{sql_string(self.catalogue.config.alias)}, "
            f"state_schema := {sql_string(_CDC_STATE_SCHEMA)}, "
            "metadata_schema := "
            f"{sql_string(self.catalogue.config.metadata_schema)})"
        ).fetchall()

    @property
    def connection(self):
        return self.catalogue.trusted_connection

    def bootstrap(self) -> None:
        self.connection.execute(
            "SELECT * FROM cdc_doctor("
            f"{sql_string(self.catalogue.config.alias)})"
        ).fetchall()

    def active_generation(self) -> ActiveGeneration | None:
        generation = state.active_generation()
        if generation is None:
            return None
        if generation.registry_digest != REGISTRY_DIGEST:
            raise RegistryMismatch(
                "active generation registry differs from this worker; "
                "a complete rebuild is required"
            )
        return generation

    def ensure_consumer(self, generation: ActiveGeneration) -> str:
        name = _consumer_name(generation.id)
        if self._consumer_exists(name):
            return name
        try:
            self.connection.execute(
                "SELECT * FROM cdc_dml_consumer_create("
                f"{sql_string(self.catalogue.config.alias)}, "
                f"{sql_string(name)}, "
                "table_name := 'ingest.visits', "
                f"start_at := {sql_string(str(generation.covered_snapshot))}, "
                "change_types := ['insert'])"
            ).fetchall()
        except duckdb.ConstraintException:
            # Horizontal replicas can observe absence together. Consumer
            # creation is idempotent only when the expected generation
            # consumer became visible after the losing create attempt.
            if not self._consumer_exists(name):
                raise
        return name

    def _consumer_exists(self, name: str) -> bool:
        rows = self.connection.execute(
            "SELECT consumer_name FROM cdc_list_consumers("
            f"{sql_string(self.catalogue.config.alias)}) "
            f"WHERE consumer_name = {sql_string(name)}"
        ).fetchall()
        return bool(rows)

    def listen(self, consumer: str) -> ChangeWindow | None:
        cursor = self.connection.execute(
            "SELECT start_snapshot, end_snapshot, snapshot_id, "
            "visit_id::VARCHAR AS visit_id "
            "FROM cdc_dml_changes_listen("
            f"{sql_string(self.catalogue.config.alias)}, "
            f"{sql_string(consumer)}, "
            f"timeout_ms := {_LISTEN_TIMEOUT_MS}, "
            f"max_snapshots := {_WINDOW_SNAPSHOTS}, "
            '"coalesce" := true, auto_commit := false) '
            "WHERE change_type = 'insert' "
            "ORDER BY snapshot_id, rowid"
        )
        rows = cursor.fetchall()
        if not rows:
            return None
        return ChangeWindow(
            start_snapshot=int(rows[0][0]),
            end_snapshot=int(rows[0][1]),
            visit_ids=tuple(dict.fromkeys(str(row[3]) for row in rows)),
        )

    def applied(self, batch_ids: tuple[UUID, ...]) -> frozenset[UUID]:
        return state.applied_batches(batch_ids)

    def heartbeat(self, consumer: str) -> None:
        self.connection.execute(
            "SELECT * FROM cdc_consumer_heartbeat("
            f"{sql_string(self.catalogue.config.alias)}, "
            f"{sql_string(consumer)})"
        ).fetchall()

    def complete_window(
        self,
        generation_id: UUID,
        consumer: str,
        end_snapshot: int,
    ) -> bool:
        with write_claims({"generation": [str(generation_id)]}):
            if not state.cover_snapshot(generation_id, end_snapshot):
                return False
            self.connection.execute(
                "SELECT * FROM cdc_commit("
                f"{sql_string(self.catalogue.config.alias)}, "
                f"{sql_string(consumer)}, {end_snapshot})"
            ).fetchall()
        return True

    def release(self, consumer: str) -> None:
        try:
            self.connection.execute(
                "SELECT * FROM cdc_consumer_release("
                f"{sql_string(self.catalogue.config.alias)}, "
                f"{sql_string(consumer)})"
            ).fetchall()
        except Exception:
            logging.exception("failed to release live CDC consumer %s", consumer)

    def close(self) -> None:
        self.catalogue.close()


def bootstrap_live_cdc() -> None:
    connection = LiveCdcConnection()
    try:
        connection.bootstrap()
    finally:
        connection.close()


async def run_elected_live_materialization(
    jetstream,
    *,
    stop: asyncio.Event,
    publish,
) -> None:
    """Elect one replaceable CDC coordinator from all worker replicas."""

    leases = await ensure_operation_lease_storage(jetstream)
    while not stop.is_set():
        try:
            async with operation_leases(
                leases,
                (_LEADER_IDENTITY,),
                phase=_LEADER_PHASE,
                acquire_timeout=0.0,
            ) as guard:
                logging.info("live materialization CDC leadership acquired")
                await _run_live_owner(
                    jetstream,
                    stop=stop,
                    guard=guard,
                    publish=publish,
                )
        except asyncio.CancelledError:
            raise
        except OperationLeaseUnavailable:
            await _wait(stop, 1)
        except OperationLeaseLost:
            logging.warning("live materialization CDC leadership lost")
            await _wait(stop, 0.1)
        except Exception:
            logging.exception("live materialization CDC election failed")
            await _wait(stop, 1)


async def _run_live_owner(
    jetstream,
    *,
    stop: asyncio.Event,
    guard: OperationLeaseGuard,
    publish,
) -> None:
    owner_stop = asyncio.Event()
    watcher = asyncio.create_task(
        _forward_owner_stop(stop, guard, owner_stop),
        name="materialization-live-cdc-leadership",
    )
    try:
        await run_live_materialization(
            jetstream,
            stop=owner_stop,
            publish=publish,
        )
    finally:
        owner_stop.set()
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


async def _forward_owner_stop(
    process_stop: asyncio.Event,
    guard: OperationLeaseGuard,
    owner_stop: asyncio.Event,
) -> None:
    process_wait = asyncio.create_task(process_stop.wait())
    lease_wait = asyncio.create_task(guard.wait_lost())
    try:
        await asyncio.wait(
            (process_wait, lease_wait),
            return_when=asyncio.FIRST_COMPLETED,
        )
        owner_stop.set()
    finally:
        for task in (process_wait, lease_wait):
            task.cancel()
        await asyncio.gather(
            process_wait,
            lease_wait,
            return_exceptions=True,
        )


async def run_live_materialization(
    jetstream,
    *,
    stop: asyncio.Event,
    publish,
) -> None:
    """Own one CDC window and fan its deterministic batches to JetStream."""

    cdc = await asyncio.to_thread(LiveCdcConnection)
    consumer: str | None = None
    try:
        while not stop.is_set():
            try:
                generation = await asyncio.to_thread(cdc.active_generation)
                if generation is None:
                    await _wait(stop, 1)
                    continue
                expected = _consumer_name(generation.id)
                if consumer != expected:
                    if consumer is not None:
                        await asyncio.to_thread(cdc.release, consumer)
                    consumer = await asyncio.to_thread(
                        cdc.ensure_consumer,
                        generation,
                    )
                window = await asyncio.to_thread(cdc.listen, consumer)
                if window is None:
                    continue
                batches = _window_batches(
                    generation.id,
                    window,
                    batch_size=generation.batch_size,
                )
                for batch in batches:
                    await publish(jetstream, batch)
                applied = await _wait_until_applied(
                    cdc,
                    generation.id,
                    consumer,
                    tuple(batch.batch_id for batch in batches),
                    stop,
                )
                if not applied:
                    continue
                current = await asyncio.to_thread(cdc.active_generation)
                if current is None or current.id != generation.id:
                    continue
                await asyncio.to_thread(
                    cdc.complete_window,
                    generation.id,
                    consumer,
                    window.end_snapshot,
                )
            except asyncio.CancelledError:
                raise
            except RegistryMismatch:
                await _wait(stop, 1)
            except Exception as exc:
                if "CDC_BUSY" not in str(exc):
                    logging.exception("live materialization CDC loop failed")
                await _wait(stop, 1)
    finally:
        if consumer is not None:
            await asyncio.to_thread(cdc.release, consumer)
        await asyncio.to_thread(cdc.close)


def _window_batches(
    generation_id: UUID,
    window: ChangeWindow,
    *,
    batch_size: int,
) -> tuple[LiveBatchWork, ...]:
    batches: list[LiveBatchWork] = []
    for ordinal, offset in enumerate(range(0, len(window.visit_ids), batch_size)):
        visit_ids = window.visit_ids[offset : offset + batch_size]
        identity = (
            f"{window.start_snapshot}:{window.end_snapshot}:{ordinal}:"
            + ",".join(visit_ids)
        )
        batches.append(
            LiveBatchWork(
                batch_id=uuid5(generation_id, identity),
                generation_id=generation_id,
                ordinal=ordinal,
                snapshot=window.end_snapshot,
                visit_ids=visit_ids,
            )
        )
    return tuple(batches)


async def _wait_until_applied(
    cdc: LiveCdcConnection,
    generation_id: UUID,
    consumer: str,
    batch_ids: tuple[UUID, ...],
    stop: asyncio.Event,
) -> bool:
    pending = set(batch_ids)
    while pending and not stop.is_set():
        current = await asyncio.to_thread(cdc.active_generation)
        if current is None or current.id != generation_id:
            return False
        applied = await asyncio.to_thread(cdc.applied, tuple(pending))
        pending.difference_update(applied)
        if pending:
            await asyncio.to_thread(cdc.heartbeat, consumer)
            await _wait(stop, _HEARTBEAT_SECONDS)
    return not pending


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


def _consumer_name(generation_id: UUID) -> str:
    return _CONSUMER_PREFIX + generation_id.hex
