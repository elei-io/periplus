"""NATS-driven whole-table materialization lifecycle."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
import os
from types import SimpleNamespace
from uuid import UUID

from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from nats.js.errors import NotFoundError
from sqlalchemy import select

from config import get_float, get_int
from config.performance import materialization_duckdb_memory_limit
from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_views.models import CatalogueViewReference
from db.session import session_scope
from materialization.dematerialization import dematerialize_one
from repository.catalogue import catalogue_from_env
from repository.catalogue.materializations import (
    MaterializationSchemaChangeError,
    MaterializationStore,
)
from repository.catalogue.views import CatalogueViewStore
from repository.ingestion.health import HealthMonitor
from runtime.catalogue_events import (
    DDL_RECONCILER_DURABLE,
    DDL_SUBJECT,
    EVENT_STREAM,
    CatalogueDDLEvent,
    CatalogueDMLTick,
    dml_subject,
    ensure_catalogue_event_stream,
)
from runtime.catalogue_workers import (
    catalogue_worker_presence,
    ensure_catalogue_worker_storage,
)
from runtime.nats_client import connect_nats
from runtime.operation_leases import (
    OperationLeaseUnavailable,
    ensure_operation_lease_storage,
    operation_leases,
)
from runtime.resource_governor import (
    DURABLE_RESOURCE_WAIT,
    catalogue_request,
    ensure_resource_governor_storage,
    object_units,
    resource_permits,
)
from workers.lifecycle import cancel_task


async def run(
    initialized: asyncio.Event | None = None, monitor: HealthMonitor | None = None
) -> None:
    client = await connect_nats()
    jetstream = client.jetstream()
    await ensure_catalogue_event_stream(jetstream)
    leases = await ensure_operation_lease_storage(jetstream)
    resources = await ensure_resource_governor_storage(jetstream)
    workers = await ensure_catalogue_worker_storage(jetstream)
    ddl_subscription = await jetstream.pull_subscribe(
        DDL_SUBJECT,
        durable=DDL_RECONCILER_DURABLE,
        stream=EVENT_STREAM,
        config=ConsumerConfig(
            durable_name=DDL_RECONCILER_DURABLE,
            deliver_policy=DeliverPolicy.ALL,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=60,
            max_ack_pending=100,
            filter_subject=DDL_SUBJECT,
        ),
    )
    catalogue = await asyncio.to_thread(
        catalogue_from_env,
        memory_limit=materialization_duckdb_memory_limit(),
    )
    subscriptions: dict[UUID, object] = {}
    stop = asyncio.Event()
    active_operation_count = [0]
    if monitor is not None:
        monitor.dependencies_ready()
        monitor.subsystem_ready("materializations")
    if initialized is not None:
        initialized.set()
    presence_task = asyncio.create_task(
        catalogue_worker_presence(
            workers,
            worker_id=f"materialization:{os.uname().nodename}:{os.getpid()}",
            capability="materialization",
            started_at=datetime.now(UTC),
            active_operation_count=lambda: active_operation_count[0],
            healthy=lambda: monitor is None or monitor.status()[0],
            stop=stop,
        )
    )
    try:
        while True:
            ddl_worked = await _reconcile_ddl(ddl_subscription)
            definitions = await asyncio.to_thread(_active_definitions)
            active_ids = {item.id for item in definitions}
            subscriptions = {
                key: value for key, value in subscriptions.items() if key in active_ids
            }
            worked = ddl_worked
            for definition in definitions:
                try:
                    if definition.desired_state == "deleting":
                        await _tracked_operation(
                            active_operation_count,
                            _delete_one(
                                jetstream,
                                catalogue,
                                leases,
                                resources,
                                definition,
                            ),
                        )
                        subscriptions.pop(definition.id, None)
                        worked = True
                        continue
                    subscription = subscriptions.get(definition.id)
                    if subscription is None:
                        subscription = await _subscription(jetstream, definition)
                        subscriptions[definition.id] = subscription
                    if definition.observed_state == "creating":
                        worked = (
                            await _tracked_operation(
                                active_operation_count,
                                _bootstrap_one(
                                    catalogue,
                                    leases,
                                    resources,
                                    definition.id,
                                ),
                            )
                            or worked
                        )
                        continue
                    if definition.desired_state == "paused":
                        await asyncio.to_thread(
                            _mark_observed, definition.id, "paused"
                        )
                        continue
                    if definition.observed_state == "paused":
                        await _tracked_operation(
                            active_operation_count,
                            _resume_one(
                                catalogue,
                                leases,
                                resources,
                                definition.id,
                            ),
                        )
                        worked = True
                        continue
                    if definition.observed_state in {"blocked_schema", "failed"}:
                        continue
                    worked = (
                        await _refresh_from_ticks(
                            active_operation_count,
                            catalogue,
                            leases,
                            resources,
                            subscription,
                            definition,
                        )
                        or worked
                    )
                except OperationLeaseUnavailable:
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logging.exception(
                        "materialization %s failed", definition.id
                    )
                    await asyncio.to_thread(_mark_failed, definition.id, exc)
            if not worked:
                await asyncio.sleep(
                    get_float("ATLAS_MATERIALIZATION_CONTROL_POLL_SECONDS")
                )
    finally:
        stop.set()
        await cancel_task(presence_task)
        await asyncio.to_thread(catalogue.close)
        await client.close()


def _active_definitions() -> list[SimpleNamespace]:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(CatalogueMaterialization)
                .where(CatalogueMaterialization.archived_at.is_(None))
                .order_by(CatalogueMaterialization.created_at)
            )
        )
        return [
            SimpleNamespace(
                id=row.id,
                name=row.name,
                desired_state=row.desired_state,
                observed_state=row.observed_state,
                source_table_uuid=row.source_table_uuid,
                nats_consumer_name=row.nats_consumer_name,
                refresh_delay_seconds=row.refresh_delay_seconds,
                bootstrap_snapshot=row.bootstrap_snapshot,
                processed_snapshot=row.processed_snapshot,
                source_schema_version=row.source_schema_version,
                ducklake_table_uuid=row.ducklake_table_uuid,
            )
            for row in rows
        ]


async def _reconcile_ddl(subscription) -> bool:
    try:
        messages = await subscription.fetch(batch=100, timeout=0.01)
    except (NatsTimeoutError, TimeoutError):
        return False
    for message in messages:
        try:
            event = CatalogueDDLEvent.model_validate_json(message.data)
            await asyncio.to_thread(_apply_ddl_event, event)
        except Exception:
            logging.exception("failed to reconcile catalogue DDL event")
            await message.nak()
            continue
        await message.ack()
    return bool(messages)


def _apply_ddl_event(event: CatalogueDDLEvent) -> None:
    if event.object_kind != "table" or event.object_id is None:
        return
    with session_scope() as session:
        models = list(
            session.scalars(
                select(CatalogueMaterialization).where(
                    CatalogueMaterialization.archived_at.is_(None),
                    (
                        (CatalogueMaterialization.source_table_id == event.object_id)
                        | (CatalogueMaterialization.target_table_id == event.object_id)
                    ),
                )
            )
        )
        for model in models:
            is_source = model.source_table_id == event.object_id
            is_target = model.target_table_id == event.object_id
            if not is_source and not is_target:
                continue
            # The source fence is inclusive: DDL at or before materialization
            # creation is part of the definition that was accepted. Target DDL
            # through bootstrap completion was emitted by Atlas itself while
            # creating and laying out the backing table. Do not mistake either
            # history for a later external schema change.
            if is_source and event.snapshot_id <= model.control_snapshot:
                continue
            if (
                is_target
                and model.bootstrap_snapshot is not None
                and event.snapshot_id <= model.bootstrap_snapshot
            ):
                continue
            if event.event_kind == "dropped" and (is_source or is_target):
                model.desired_state = "deleting"
                model.observed_state = "deleting"
                model.last_error = (
                    f"DuckLake table {event.schema_name}.{event.object_name} was dropped."
                )
            elif event.event_kind in {"altered", "renamed"}:
                model.observed_state = "blocked_schema"
                role = "Driving table" if is_source else "Materialization target"
                model.last_error = (
                    f"{role} {event.schema_name}.{event.object_name} "
                    f"was {event.event_kind}; "
                    "create a new materialization incarnation."
                )


async def _subscription(jetstream, definition):
    config = ConsumerConfig(
        durable_name=definition.nats_consumer_name,
        deliver_policy=DeliverPolicy.NEW,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=3600,
        max_ack_pending=1000,
        filter_subject=dml_subject(definition.source_table_uuid),
    )
    return await jetstream.pull_subscribe(
        dml_subject(definition.source_table_uuid),
        durable=definition.nats_consumer_name,
        stream=EVENT_STREAM,
        config=config,
    )


async def _bootstrap_one(
    catalogue, leases, resources, materialization_id: UUID
) -> bool:
    async with operation_leases(
        leases,
        (str(materialization_id),),
        phase="materialization",
        acquire_timeout=0,
    ):
        async with _materialization_permit(
            resources, materialization_id, "bootstrap"
        ):
            await asyncio.to_thread(
                _bootstrap_materialization, catalogue, materialization_id
            )
    return True


async def _resume_one(
    catalogue,
    leases,
    resources,
    materialization_id: UUID,
) -> None:
    async with operation_leases(
        leases,
        (str(materialization_id),),
        phase="materialization",
        acquire_timeout=0,
    ):
        async with _materialization_permit(
            resources, materialization_id, "resume"
        ):
            await asyncio.to_thread(
                _resume_materialization,
                catalogue,
                materialization_id,
            )


async def _tracked_operation(counter: list[int], operation):
    counter[0] += 1
    try:
        return await operation
    finally:
        counter[0] -= 1


def _bootstrap_materialization(catalogue, materialization_id: UUID) -> None:
    with session_scope() as session:
        model = session.get(CatalogueMaterialization, materialization_id)
        if (
            model is None
            or model.archived_at is not None
            or model.observed_state != "creating"
            or model.desired_state == "deleting"
        ):
            return
        reference = session.get(CatalogueViewReference, model.view_reference_id)
        if reference is None:
            raise RuntimeError("Materialization source view reference is missing.")
        view_store = CatalogueViewStore(catalogue)
        # Recover a process death between physical creation and the Postgres update.
        present = next(
            (
                table
                for table in catalogue.lake.table.list(
                    schema_name="_atlas_materializations"
                )
                if table.table_name == model.name
            ),
            None,
        )
        if present is not None and model.ducklake_table_uuid is None:
            current = _source_view(view_store, reference, model)
            restored = view_store.replace(
                current_uuid=current.view_uuid, sql=model.source_sql
            )
            reference.ducklake_view_uuid = restored.view_uuid
            model.source_view_uuid = restored.view_uuid
            table = MaterializationStore(catalogue).inspect(model.name)
            MaterializationStore(catalogue).drop(
                name=model.name, expected_uuid=table.table_uuid
            )
        current = _source_view(view_store, reference, model)
        store = MaterializationStore(catalogue)
        table, source_snapshot = store.create_full(
            name=model.name, sql=model.source_sql
        )
        if model.partition_column:
            table = store.set_daily_partition(
                name=model.name, column=model.partition_column
            )
        wrapper = view_store.replace(
            current_uuid=reference.ducklake_view_uuid,
            sql=_backing_view_sql(catalogue, model.name),
        )
        reference.ducklake_view_uuid = wrapper.view_uuid
        model.target_table_id = table.table_id
        model.ducklake_table_uuid = table.table_uuid
        model.bootstrap_snapshot = catalogue.latest_snapshot() or source_snapshot
        model.processed_snapshot = source_snapshot
        model.observed_state = (
            "live" if model.desired_state == "live" else "paused"
        )
        model.last_refreshed_at = datetime.now(UTC)
        model.last_error = None
        session.flush()


def _source_view(view_store, reference, model):
    current = view_store.get(reference.ducklake_view_uuid)
    if current is None:
        current = next(
            (
                view
                for view in view_store.list()
                if view.view_name == reference.view_name
            ),
            None,
        )
    if current is None:
        current = view_store.create(
            name=reference.view_name,
            sql=model.source_sql,
        )
    reference.ducklake_view_uuid = current.view_uuid
    model.source_view_uuid = current.view_uuid
    return current


async def _refresh_from_ticks(
    active_operation_count,
    catalogue,
    leases,
    resources,
    subscription,
    definition,
) -> bool:
    async with operation_leases(
        leases,
        (str(definition.id),),
        phase="materialization",
        acquire_timeout=0,
    ):
        return await _refresh_from_ticks_owned(
            active_operation_count,
            catalogue,
            resources,
            subscription,
            definition,
        )


async def _refresh_from_ticks_owned(
    active_operation_count,
    catalogue,
    resources,
    subscription,
    definition,
) -> bool:
    try:
        messages = await subscription.fetch(batch=100, timeout=0.1)
    except (NatsTimeoutError, TimeoutError):
        return False
    if not messages:
        return False
    return await _tracked_operation(
        active_operation_count,
        _apply_ticks(
            catalogue,
            resources,
            subscription,
            definition,
            messages,
        ),
    )


async def _apply_ticks(
    catalogue,
    resources,
    subscription,
    definition,
    messages,
) -> bool:
    if definition.refresh_delay_seconds:
        await asyncio.sleep(definition.refresh_delay_seconds)
    while len(messages) < 1000:
        try:
            more = await subscription.fetch(
                batch=min(100, 1000 - len(messages)), timeout=0.01
            )
        except (NatsTimeoutError, TimeoutError):
            break
        if not more:
            break
        messages.extend(more)
    ticks = [CatalogueDMLTick.model_validate_json(item.data) for item in messages]
    fresh = [
        tick
        for tick in ticks
        if definition.processed_snapshot is None
        or tick.snapshot_id > definition.processed_snapshot
    ]
    if not fresh:
        for message in messages:
            await message.ack()
        return True
    schema_versions = {tick.schema_version for tick in fresh}
    baseline = definition.source_schema_version
    if baseline is not None and schema_versions != {baseline}:
        await asyncio.to_thread(
            _mark_blocked,
            definition.id,
            "The driving table schema changed; create a new materialization incarnation.",
        )
        return True
    async with _materialization_permit(
        resources, definition.id, "refresh"
    ):
        await asyncio.to_thread(
            _refresh_materialization,
            catalogue,
            definition.id,
            max(tick.snapshot_id for tick in fresh),
            next(iter(schema_versions)),
        )
    for message in messages:
        await message.ack()
    return True


def _refresh_materialization(
    catalogue,
    materialization_id: UUID,
    processed_snapshot: int,
    schema_version: int | None,
) -> None:
    with session_scope() as session:
        model = session.get(CatalogueMaterialization, materialization_id)
        if (
            model is None
            or model.archived_at is not None
            or model.desired_state != "live"
            or model.ducklake_table_uuid is None
        ):
            return
        MaterializationStore(catalogue).refresh_full(
            name=model.name,
            expected_uuid=model.ducklake_table_uuid,
            sql=model.source_sql,
        )
        if schema_version is not None:
            model.source_schema_version = (
                model.source_schema_version or schema_version
            )
        model.processed_snapshot = processed_snapshot
        model.last_refreshed_at = datetime.now(UTC)
        model.observed_state = "live"
        model.last_error = None
        session.flush()


def _resume_materialization(catalogue, materialization_id: UUID) -> None:
    _refresh_materialization(
        catalogue,
        materialization_id,
        catalogue.latest_snapshot() or 0,
        None,
    )


async def _delete_one(
    jetstream, catalogue, leases, resources, definition
) -> None:
    try:
        await jetstream.delete_consumer(
            EVENT_STREAM, definition.nats_consumer_name
        )
    except NotFoundError:
        pass
    async with operation_leases(
        leases,
        (str(definition.id),),
        phase="materialization",
        acquire_timeout=0,
    ):
        async with _materialization_permit(
            resources, definition.id, "dematerialize"
        ):
            await asyncio.to_thread(
                dematerialize_one, catalogue, definition.id
            )


def _materialization_permit(resources, materialization_id: UUID, phase: str):
    units = object_units(get_int("ATLAS_MATERIALIZATION_REFRESH_MAX_BYTES"))
    return resource_permits(
        resources,
        catalogue_request(
            f"materialization:{materialization_id}:{phase}",
            service_class="live",
            object_read_units=units,
            object_write_units=units,
        ),
        acquire_timeout=DURABLE_RESOURCE_WAIT,
    )


def _mark_observed(materialization_id: UUID, state: str) -> None:
    with session_scope() as session:
        model = session.get(CatalogueMaterialization, materialization_id)
        if model is not None and model.archived_at is None:
            model.observed_state = state


def _mark_blocked(materialization_id: UUID, error: str) -> None:
    with session_scope() as session:
        model = session.get(CatalogueMaterialization, materialization_id)
        if model is not None and model.archived_at is None:
            model.observed_state = "blocked_schema"
            model.last_error = error


def _mark_failed(materialization_id: UUID, error: Exception) -> None:
    with session_scope() as session:
        model = session.get(CatalogueMaterialization, materialization_id)
        if model is not None and model.archived_at is None:
            model.observed_state = (
                "blocked_schema"
                if isinstance(error, MaterializationSchemaChangeError)
                else "failed"
            )
            model.last_error = str(error)[:4000]


def _backing_view_sql(catalogue, name: str) -> str:
    qualified = ".".join(
        '"' + part.replace('"', '""') + '"'
        for part in (
            catalogue.config.alias,
            "_atlas_materializations",
            name,
        )
    )
    return f"SELECT * FROM {qualified}"
