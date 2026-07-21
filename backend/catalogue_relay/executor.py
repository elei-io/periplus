"""Publish DuckLake table ticks and DDL changes to durable NATS subjects."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time
from uuid import UUID

from ducklake_cdc_client import DDLConsumer, DMLConsumer

from config import get_float
from observability import catalogue_event_metrics
from repository.catalogue import Catalogue, catalogue_from_env
from repository.catalogue.cdc import validate_cdc_extension
from repository.ingestion.health import HealthMonitor
from runtime.catalogue_events import (
    DDL_SUBJECT,
    CatalogueDDLEvent,
    CatalogueDMLTick,
    dml_subject,
    ensure_catalogue_event_stream,
    relay_ddl_consumer,
    relay_dml_consumer,
)
from runtime.nats_client import connect_nats


@dataclass(frozen=True, slots=True)
class RelayedTable:
    table_id: int
    table_uuid: UUID
    schema_name: str
    table_name: str
    is_live: bool


def load_relayed_tables(catalogue: Catalogue) -> dict[int, RelayedTable]:
    metadata = _quote_identifier(f"__ducklake_metadata_{catalogue.config.alias}")
    metadata_schema = _quote_identifier(catalogue.metadata_schema)
    rows = catalogue.connection.execute(
        f"""
        WITH latest_tables AS (
            SELECT *,
                   row_number() OVER (
                       PARTITION BY table_id ORDER BY begin_snapshot DESC
                   ) AS version_rank
            FROM {metadata}.{metadata_schema}.ducklake_table
        ),
        latest_schemas AS (
            SELECT *,
                   row_number() OVER (
                       PARTITION BY schema_id ORDER BY begin_snapshot DESC
                   ) AS version_rank
            FROM {metadata}.{metadata_schema}.ducklake_schema
        )
        SELECT t.table_id,
               t.table_uuid,
               s.schema_name,
               t.table_name,
               t.end_snapshot IS NULL AND s.end_snapshot IS NULL AS is_live
        FROM latest_tables AS t
        JOIN latest_schemas AS s USING (schema_id)
        WHERE t.version_rank = 1 AND s.version_rank = 1
        ORDER BY t.table_id
        """
    ).fetchall()
    return {
        int(row[0]): RelayedTable(
            table_id=int(row[0]),
            table_uuid=UUID(str(row[1])),
            schema_name=str(row[2]),
            table_name=str(row[3]),
            is_live=bool(row[4]),
        )
        for row in rows
    }


def _open_dml(catalogue: Catalogue) -> DMLConsumer:
    return DMLConsumer(
        catalogue.lake,
        relay_dml_consumer(),
        mode="ticks",
        start_at="now",
        on_exists="use",
        lease_policy="wait",
        # Reuse Catalogue's sole connection for the global DML cursor and its
        # sequential table-identity lookups. DDLConsumer derives the relay's
        # second and only other DuckDB connection.
        connection=catalogue.connection,
    ).open()


def _open_ddl(catalogue: Catalogue) -> DDLConsumer:
    return DDLConsumer(
        catalogue.lake,
        relay_ddl_consumer(),
        mode="changes",
        start_at="now",
        on_exists="use",
        lease_policy="wait",
    ).open()


def _close(consumer: DMLConsumer | DDLConsumer) -> None:
    consumer.close(timeout=5.0, cancel=False, release=True)


async def run(
    *,
    initialized: asyncio.Event | None = None,
    monitor: HealthMonitor | None = None,
) -> None:
    client = await connect_nats()
    jetstream = client.jetstream()
    await ensure_catalogue_event_stream(jetstream)
    catalogue = await asyncio.to_thread(catalogue_from_env)
    dml: DMLConsumer | None = None
    ddl: DDLConsumer | None = None
    tables: dict[int, RelayedTable] = {}
    try:
        await asyncio.to_thread(validate_cdc_extension, catalogue)
        dml = await asyncio.to_thread(_open_dml, catalogue)
        ddl = await asyncio.to_thread(_open_ddl, catalogue)
        assert dml is not None and ddl is not None
        tables = await _reconcile(catalogue, tables)
        if monitor is not None:
            monitor.dependencies_ready()
            monitor.subsystem_ready("catalogue_cdc")
            monitor.subsystem_ready("catalogue_publication")
        if initialized is not None:
            initialized.set()
        reconcile_at = 0.0
        loop = asyncio.get_running_loop()
        while True:
            if loop.time() >= reconcile_at:
                tables = await _reconcile(catalogue, tables)
                reconcile_at = loop.time() + get_float(
                    "ATLAS_CATALOGUE_RELAY_RECONCILE_SECONDS"
                )
            worked = await _publish_ddl(jetstream, ddl)
            dml_worked, tables = await _publish_dml(
                jetstream,
                catalogue,
                tables,
                dml,
            )
            worked = dml_worked or worked
            if not worked:
                await asyncio.sleep(0.1)
    except Exception as exc:
        catalogue_event_metrics.failure(phase="relay")
        if monitor is not None:
            monitor.subsystem_unavailable(
                "catalogue_publication",
                str(exc) or type(exc).__name__,
            )
        raise
    finally:
        closing = []
        if dml is not None:
            closing.append(asyncio.to_thread(_close, dml))
        if ddl is not None:
            closing.append(asyncio.to_thread(_close, ddl))
        if closing:
            results = await asyncio.gather(*closing, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    logging.error(
                        "failed to release catalogue relay consumer",
                        exc_info=result,
                    )
        await asyncio.to_thread(catalogue.close)
        await client.close()


async def _reconcile(
    catalogue: Catalogue,
    previous: dict[int, RelayedTable],
) -> dict[int, RelayedTable]:
    tables = await asyncio.to_thread(load_relayed_tables, catalogue)
    for table_id, table in previous.items():
        current = tables.get(table_id)
        if table.is_live and (current is None or not current.is_live):
            catalogue_event_metrics.remove_table(
                table_uuid=str(table.table_uuid),
                schema_name=table.schema_name,
                table_name=table.table_name,
            )
    for table_id, table in tables.items():
        old = previous.get(table_id)
        if old == table:
            continue
        if old is not None and old.is_live:
            catalogue_event_metrics.remove_table(
                table_uuid=str(old.table_uuid),
                schema_name=old.schema_name,
                table_name=old.table_name,
            )
        if not table.is_live:
            continue
        catalogue_event_metrics.track_table(
            table_uuid=str(table.table_uuid),
            schema_name=table.schema_name,
            table_name=table.table_name,
        )
        if old is None:
            logging.info(
                "discovered relayed table %s.%s (%s)",
                table.schema_name,
                table.table_name,
                table.table_uuid,
            )
        else:
            logging.info(
                "updated relayed table identity metadata from %s.%s to %s.%s (%s)",
                old.schema_name,
                old.table_name,
                table.schema_name,
                table.table_name,
                table.table_uuid,
            )
    catalogue_event_metrics.table_count(
        sum(table.is_live for table in tables.values())
    )
    return tables


async def _publish_dml(
    jetstream,
    catalogue: Catalogue,
    tables: dict[int, RelayedTable],
    consumer: DMLConsumer,
) -> tuple[bool, dict[int, RelayedTable]]:
    batch = await asyncio.to_thread(consumer.read, max_snapshots=100)
    if batch is None:
        return False, tables
    touched_ids = {
        int(table_id)
        for tick in batch.ticks
        for table_id in tick.table_ids
    }
    if touched_ids - tables.keys():
        tables = await _reconcile(catalogue, tables)
    unresolved = touched_ids - tables.keys()
    if unresolved:
        raise RuntimeError(
            "DuckLake DML tick referenced unknown table IDs "
            + ", ".join(str(table_id) for table_id in sorted(unresolved))
        )
    for tick in batch.ticks:
        for table_id in tick.table_ids:
            table = tables[int(table_id)]
            if table.is_live:
                catalogue_event_metrics.source_tick(
                    table_uuid=str(table.table_uuid),
                    schema_name=table.schema_name,
                    table_name=table.table_name,
                    snapshot_id=tick.snapshot_id,
                )
            event = CatalogueDMLTick(
                table_id=table.table_id,
                table_uuid=table.table_uuid,
                schema_name=table.schema_name,
                table_name=table.table_name,
                snapshot_id=tick.snapshot_id,
                snapshot_time=tick.snapshot_time,
                schema_version=tick.schema_version,
            )
            await _publish(
                jetstream,
                dml_subject(table.table_uuid),
                event.model_dump_json().encode(),
                kind="dml",
                headers={"Nats-Msg-Id": event.message_id},
            )
            if table.is_live:
                catalogue_event_metrics.published_tick(
                    table_uuid=str(table.table_uuid),
                    schema_name=table.schema_name,
                    table_name=table.table_name,
                    snapshot_id=event.snapshot_id,
                    snapshot_time=event.snapshot_time,
                )
    await asyncio.to_thread(batch.commit)
    return True, tables


async def _publish_ddl(jetstream, consumer: DDLConsumer) -> bool:
    batch = await asyncio.to_thread(consumer.read, max_snapshots=100)
    if batch is None:
        return False
    for change in batch.changes:
        event = CatalogueDDLEvent(
            event_kind=_enum_value(change.event_kind),
            object_kind=_enum_value(change.object_kind),
            snapshot_id=change.snapshot_id,
            snapshot_time=change.snapshot_time,
            schema_id=change.schema_id,
            schema_name=change.schema_name,
            object_id=change.object_id,
            object_name=change.object_name,
            details=change.details,
        )
        await _publish(
            jetstream,
            DDL_SUBJECT,
            event.model_dump_json().encode(),
            kind="ddl",
            headers={"Nats-Msg-Id": event.message_id},
        )
        catalogue_event_metrics.published_ddl(
            snapshot_id=event.snapshot_id,
            snapshot_time=event.snapshot_time,
        )
    await asyncio.to_thread(batch.commit)
    return True


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


async def _publish(
    jetstream,
    subject: str,
    payload: bytes,
    *,
    kind: str,
    headers: dict[str, str],
) -> None:
    started = time.perf_counter()
    try:
        await jetstream.publish(subject, payload, headers=headers)
    except Exception:
        catalogue_event_metrics.publication(
            kind=kind,
            outcome="failed",
            duration_seconds=time.perf_counter() - started,
        )
        raise
    catalogue_event_metrics.publication(
        kind=kind,
        outcome="succeeded",
        duration_seconds=time.perf_counter() - started,
    )
