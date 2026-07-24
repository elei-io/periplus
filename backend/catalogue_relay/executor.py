"""Ingress Basin-owned DuckLake CDC into Atlas-owned catalogue events."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
import logging
import time
from uuid import UUID

import duckdb
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from nats.errors import Error as NatsError
from pydantic import BaseModel, ConfigDict

from observability import catalogue_event_metrics
from repository.catalogue import Catalogue, catalogue_from_env
from repository.ingestion.health import HealthMonitor
from runtime.catalogue_events import (
    DDL_SUBJECT,
    CatalogueDDLEvent,
    CatalogueDMLTick,
    basin_ddl_durable,
    basin_ddl_subject,
    basin_dml_durable,
    basin_dml_subject,
    dml_subject,
    ensure_catalogue_event_stream,
)
from runtime.nats_client import connect_basin_nats, connect_nats


_FETCH_BATCH = 100
_FETCH_TIMEOUT_SECONDS = 60.0
_SOURCE_ACK_WAIT_SECONDS = 60
_TRANSIENT_RETRY_INITIAL_SECONDS = 0.25
_TRANSIENT_RETRY_MAX_SECONDS = 5.0


class BasinDDLEvent(BaseModel):
    """The stable subset Atlas consumes from Basin's 1:1 CDC payload."""

    model_config = ConfigDict(extra="allow")

    snapshot_id: int
    snapshot_time: datetime | None
    event_kind: str
    object_kind: str
    schema_id: int | None
    schema_name: str | None
    object_id: int | None
    object_name: str | None
    details: str | None


class BasinDMLTick(BaseModel):
    """The stable subset Atlas consumes from Basin's 1:1 CDC payload."""

    model_config = ConfigDict(extra="allow")

    snapshot_id: int
    snapshot_time: datetime | None
    schema_version: int
    table_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RelayedTable:
    table_id: int
    table_uuid: UUID
    schema_name: str
    table_name: str


class TableResolver:
    """Resolve Basin's physical table IDs to Atlas's stable event identity."""

    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue
        self._tables: dict[int, RelayedTable] | None = None
        self.retired_table_ids: set[int] = set()

    def invalidate(self) -> None:
        self._tables = None

    def note_ddl(self, event: BasinDDLEvent) -> None:
        if event.object_kind.lower() != "table" or event.object_id is None:
            return
        self.invalidate()
        if event.event_kind.lower() in {"deleted", "dropped", "removed"}:
            self.retired_table_ids.add(event.object_id)
        elif event.event_kind.lower() in {"created", "restored"}:
            self.retired_table_ids.discard(event.object_id)

    def resolve(self, table_ids: set[int]) -> dict[int, RelayedTable]:
        if not table_ids:
            return {}
        if self._tables is None or table_ids - self._tables.keys():
            self._tables = load_relayed_tables(self.catalogue)
        unresolved = table_ids - self._tables.keys() - self.retired_table_ids
        if unresolved:
            # Basin retains DML independently from DDL. After a relay restart,
            # an old DML tick can therefore refer to a table that has since
            # been dropped even though this process did not observe the
            # already-acknowledged drop event. DuckLake's current public table
            # inventory is authoritative for whether the identity is live.
            self.retired_table_ids.update(unresolved)
            logging.info(
                "ignoring historical Basin DML for absent DuckLake table IDs: %s",
                ", ".join(str(table_id) for table_id in sorted(unresolved)),
            )
        return {
            table_id: table
            for table_id in table_ids
            if (table := self._tables.get(table_id)) is not None
        }


def load_relayed_tables(catalogue: Catalogue) -> dict[int, RelayedTable]:
    """Load current DuckLake identities without reading its control catalogue."""

    alias = _quote_literal(catalogue.config.alias)
    rows = catalogue.remote_rows(
        f"""
        WITH names AS (
            SELECT table_name,
                   min(table_schema) AS schema_name,
                   count(*) AS name_count
            FROM information_schema.tables
            WHERE table_catalog = {alias}
              AND table_type = 'BASE TABLE'
            GROUP BY table_name
        )
        SELECT tables.table_id,
               tables.table_uuid,
               names.schema_name,
               tables.table_name,
               names.name_count
        FROM ducklake_table_info({alias}) AS tables
        JOIN names USING (table_name)
        ORDER BY tables.table_id
        """
    )
    ambiguous = [str(row[3]) for row in rows if int(row[4]) != 1]
    if ambiguous:
        raise RuntimeError(
            "DuckLake table names must be unique across schemas until Quack "
            "preserves schema qualification: "
            + ", ".join(sorted(ambiguous))
        )
    return {
        int(row[0]): RelayedTable(
            table_id=int(row[0]),
            table_uuid=UUID(str(row[1])),
            schema_name=str(row[2]),
            table_name=str(row[3]),
        )
        for row in rows
    }


async def run(
    *,
    initialized: asyncio.Event | None = None,
    monitor: HealthMonitor | None = None,
) -> None:
    basin_client = await connect_basin_nats()
    atlas_client = await connect_nats()
    basin_jetstream = basin_client.jetstream()
    atlas_jetstream = atlas_client.jetstream()
    catalogue: Catalogue | None = None
    pulls: dict[str, asyncio.Task] = {}
    try:
        await ensure_catalogue_event_stream(atlas_jetstream)
        catalogue = await asyncio.to_thread(catalogue_from_env)
        lake = catalogue.lake_slug
        ddl_subscription = await _source_subscription(
            basin_jetstream,
            subject=basin_ddl_subject(lake),
            durable=basin_ddl_durable(lake),
        )
        dml_subscription = await _source_subscription(
            basin_jetstream,
            subject=basin_dml_subject(lake),
            durable=basin_dml_durable(lake),
        )
        resolver = TableResolver(catalogue)

        # Establish table lifecycle before replaying historical DML from the
        # independently retained source stream.
        await _drain_ddl_backlog(
            ddl_subscription,
            atlas_jetstream,
            resolver,
        )
        await asyncio.to_thread(resolver.resolve, set())
        if monitor is not None:
            monitor.dependencies_ready()
            monitor.subsystem_ready("basin_cdc")
            monitor.subsystem_ready("atlas_catalogue_publication")
        if initialized is not None:
            initialized.set()

        pulls = {
            "ddl": asyncio.create_task(
                _fetch(ddl_subscription), name="basin-ddl-pull"
            ),
            "dml": asyncio.create_task(
                _fetch(dml_subscription), name="basin-dml-pull"
            ),
        }
        while True:
            await asyncio.wait(
                set(pulls.values()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            ddl_task = pulls["ddl"]
            if ddl_task.done():
                for message in ddl_task.result():
                    await _publish_ddl_message(
                        atlas_jetstream, resolver, message
                    )
                pulls["ddl"] = asyncio.create_task(
                    _fetch(ddl_subscription), name="basin-ddl-pull"
                )
            dml_task = pulls["dml"]
            if dml_task.done():
                for message in dml_task.result():
                    await _publish_dml_message(
                        atlas_jetstream, resolver, message
                    )
                pulls["dml"] = asyncio.create_task(
                    _fetch(dml_subscription), name="basin-dml-pull"
                )
            if monitor is not None:
                monitor.heartbeat()
    except Exception as exc:
        catalogue_event_metrics.failure(phase="basin_ingress")
        if monitor is not None:
            monitor.subsystem_unavailable(
                "atlas_catalogue_publication",
                str(exc) or type(exc).__name__,
            )
        raise
    finally:
        for pull in pulls.values():
            pull.cancel()
        await asyncio.gather(
            *pulls.values(),
            return_exceptions=True,
        )
        if catalogue is not None:
            await asyncio.to_thread(catalogue.close)
        await asyncio.gather(
            basin_client.close(),
            atlas_client.close(),
            return_exceptions=True,
        )


async def _source_subscription(jetstream, *, subject: str, durable: str):
    stream = await jetstream.find_stream_name_by_subject(subject)
    return await jetstream.pull_subscribe(
        subject,
        durable=durable,
        stream=stream,
        config=ConsumerConfig(
            durable_name=durable,
            deliver_policy=DeliverPolicy.ALL,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=_SOURCE_ACK_WAIT_SECONDS,
            max_ack_pending=_FETCH_BATCH,
            filter_subject=subject,
        ),
    )


async def _drain_ddl_backlog(
    subscription,
    atlas_jetstream,
    resolver: TableResolver,
) -> None:
    while True:
        info = await subscription.consumer_info()
        if info.num_pending <= 0:
            return
        messages = await subscription.fetch(
            batch=min(_FETCH_BATCH, info.num_pending),
            timeout=_FETCH_TIMEOUT_SECONDS,
        )
        for message in messages:
            await _publish_ddl_message(atlas_jetstream, resolver, message)


async def _consume_ddl(
    subscription,
    atlas_jetstream,
    resolver: TableResolver,
) -> bool:
    messages = await _fetch(subscription)
    for message in messages:
        await _publish_ddl_message(atlas_jetstream, resolver, message)
    return bool(messages)


async def _consume_dml(
    subscription,
    atlas_jetstream,
    resolver: TableResolver,
) -> bool:
    messages = await _fetch(subscription)
    for message in messages:
        await _publish_dml_message(atlas_jetstream, resolver, message)
    return bool(messages)


async def _fetch(subscription) -> list:
    try:
        return await subscription.fetch(
            batch=_FETCH_BATCH,
            timeout=_FETCH_TIMEOUT_SECONDS,
        )
    except (TimeoutError, asyncio.TimeoutError):
        return []


async def _publish_ddl_message(
    atlas_jetstream,
    resolver: TableResolver,
    message,
) -> None:
    source = BasinDDLEvent.model_validate_json(message.data)
    event = CatalogueDDLEvent(
        event_kind=source.event_kind,
        object_kind=source.object_kind,
        snapshot_id=source.snapshot_id,
        snapshot_time=source.snapshot_time,
        schema_id=source.schema_id,
        schema_name=source.schema_name,
        object_id=source.object_id,
        object_name=source.object_name,
        details=source.details,
    )
    await _publish(
        atlas_jetstream,
        DDL_SUBJECT,
        event.model_dump_json().encode(),
        kind="ddl",
        headers={"Nats-Msg-Id": event.message_id},
    )
    resolver.note_ddl(source)
    await message.ack()
    catalogue_event_metrics.published_ddl(
        snapshot_id=event.snapshot_id,
        snapshot_time=event.snapshot_time,
    )


async def _publish_dml_message(
    atlas_jetstream,
    resolver: TableResolver,
    message,
) -> None:
    source = BasinDMLTick.model_validate_json(message.data)
    table_ids = set(source.table_ids)
    tables = await _retry_transient_source_operation(
        lambda: asyncio.to_thread(resolver.resolve, table_ids),
        message=message,
        operation_name="resolve Basin DML table identities",
        on_retry=resolver.invalidate,
    )
    for table_id in source.table_ids:
        table = tables.get(table_id)
        if table is None:
            logging.info(
                "ignoring Basin DML for retired DuckLake table ID %s",
                table_id,
            )
            continue
        catalogue_event_metrics.source_tick(
            table_uuid=str(table.table_uuid),
            schema_name=table.schema_name,
            table_name=table.table_name,
            snapshot_id=source.snapshot_id,
        )
        event = CatalogueDMLTick(
            table_id=table.table_id,
            table_uuid=table.table_uuid,
            schema_name=table.schema_name,
            table_name=table.table_name,
            snapshot_id=source.snapshot_id,
            snapshot_time=source.snapshot_time,
            schema_version=source.schema_version,
        )
        await _publish(
            atlas_jetstream,
            dml_subject(table.table_uuid),
            event.model_dump_json().encode(),
            kind="dml",
            headers={"Nats-Msg-Id": event.message_id},
        )
        catalogue_event_metrics.published_tick(
            table_uuid=str(table.table_uuid),
            schema_name=table.schema_name,
            table_name=table.table_name,
            snapshot_id=event.snapshot_id,
            snapshot_time=event.snapshot_time,
        )
    await message.ack()


async def _retry_transient_source_operation[T](
    operation: Callable[[], Awaitable[T]],
    *,
    message,
    operation_name: str,
    on_retry: Callable[[], None] | None = None,
) -> T:
    """Keep a source delivery alive while a remote dependency recovers."""

    delay = _TRANSIENT_RETRY_INITIAL_SECONDS
    while True:
        try:
            return await operation()
        except (duckdb.IOException, NatsError) as exc:
            catalogue_event_metrics.failure(phase="basin_ingress")
            logging.warning(
                "%s failed transiently; retrying in %.2fs: %s",
                operation_name,
                delay,
                exc,
            )
            if on_retry is not None:
                on_retry()
            try:
                await message.in_progress()
            except NatsError:
                # Redelivery remains safe because Atlas publications carry
                # deterministic JetStream message IDs.
                pass
            await asyncio.sleep(delay)
            delay = min(delay * 2, _TRANSIENT_RETRY_MAX_SECONDS)


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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
