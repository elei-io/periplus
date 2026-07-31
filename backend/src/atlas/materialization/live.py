"""Durable CDC ownership for active-generation materialization."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID, uuid5

from atlas.materialization.contracts import LiveBatchWork
from atlas.materialization.sql import sql_string, sql_string_list
from atlas.platform.catalogue import catalogue_from_env

_CDC_STATE_SCHEMA = "atlas_ducklake_cdc"
_CONSUMER_PREFIX = "atlas_live_visits_"
_WINDOW_SNAPSHOTS = 100
_LISTEN_TIMEOUT_MS = 1_000
_HEARTBEAT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class ActiveGeneration:
    id: UUID
    covered_snapshot: int
    batch_size: int


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
        rows = self.catalogue.trusted_remote_rows(
            "SELECT generation_id::VARCHAR, covered_snapshot, batch_size "
            "FROM material._atlas_materialization_state "
            "ORDER BY activated_at DESC LIMIT 1"
        )
        if not rows:
            return None
        return ActiveGeneration(
            UUID(str(rows[0][0])),
            int(rows[0][1]),
            int(rows[0][2]),
        )

    def ensure_consumer(self, generation: ActiveGeneration) -> str:
        name = _consumer_name(generation.id)
        rows = self.connection.execute(
            "SELECT consumer_name FROM cdc_list_consumers("
            f"{sql_string(self.catalogue.config.alias)}) "
            f"WHERE consumer_name = {sql_string(name)}"
        ).fetchall()
        if rows:
            return name
        self.connection.execute(
            "SELECT * FROM cdc_dml_consumer_create("
            f"{sql_string(self.catalogue.config.alias)}, "
            f"{sql_string(name)}, "
            "table_name := 'ingest.visits', "
            f"start_at := {sql_string(str(generation.covered_snapshot))}, "
            "change_types := ['insert'])"
        ).fetchall()
        return name

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
        if not batch_ids:
            return frozenset()
        rows = self.catalogue.trusted_remote_rows(
            "SELECT batch_id::VARCHAR "
            "FROM material._atlas_applied_batches "
            f"WHERE batch_id IN ({sql_string_list({str(value) for value in batch_ids})})"
        )
        return frozenset(UUID(str(row[0])) for row in rows)

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
        with self.catalogue.remote_transaction():
            current = self.active_generation()
            if current is None or current.id != generation_id:
                return False
            self.catalogue.trusted_remote_execute(
                "UPDATE material._atlas_materialization_state "
                f"SET covered_snapshot = {end_snapshot} "
                f"WHERE generation_id = UUID {sql_string(str(generation_id))}"
            )
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
