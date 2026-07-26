"""NATS-driven materialization lifecycle and bounded refresh execution."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
from types import SimpleNamespace
from collections.abc import Callable
from uuid import UUID

from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from nats.js.errors import NotFoundError
from sqlalchemy import select

from config import get_float
from config.performance import materialization_duckdb_memory_limit
from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_views.models import CatalogueViewReference
from db.session import session_scope
from materialization.dematerialization import dematerialize_one
from repository.catalogue import catalogue_from_env
from repository.catalogue.materializations import (
    MaterializationError,
    MaterializationSchemaChangeError,
    MaterializationStore,
    physical_materialization_name,
)
from repository.catalogue.operations import (
    is_retryable_catalogue_transaction_conflict,
    run_with_catalogue_retry,
)
from repository.catalogue.compiler_definitions import (
    CatalogueCompilerSnapshotChanged,
    read_catalogue_compiler_definitions,
)
from repository.catalogue.views import CatalogueViewStore
from repository.ingestion.health import HealthMonitor
from runtime.catalogue_events import (
    DDL_RECONCILER_DURABLE,
    DDL_SUBJECT,
    DML_SUBJECT_PREFIX,
    EVENT_STREAM,
    MATERIALIZATION_DURABLE_PREFIX,
    CatalogueDDLEvent,
    CatalogueDMLTick,
    dml_subject,
    ensure_catalogue_event_stream,
)
from runtime.catalogue_workers import CatalogueLaneReporter
from runtime.nats_client import connect_nats
from runtime.operation_leases import (
    OperationLeaseLost,
    OperationLeaseUnavailable,
    ensure_operation_lease_storage,
    operation_leases,
)
from workers.lifecycle import cancel_task


class MaterializationControlChanged(RuntimeError):
    """The Postgres definition changed while remote work was in flight."""


def _materialization_store(catalogue) -> MaterializationStore:
    """Build a store with one authoritative macro-definition snapshot."""

    # Pin the definition and physical-metadata reads to one remote snapshot.
    # Concurrent materialization DML advances the lake snapshot frequently;
    # it must not look like concurrent catalogue DDL to this reader.
    with catalogue.remote_transaction():
        definitions = read_catalogue_compiler_definitions(
            catalogue.trusted_connection,
            catalogue_alias=catalogue.config.alias,
        )
    return MaterializationStore(
        catalogue,
        scalar_macros=definitions.scalar_macros,
        table_macros=definitions.table_macros,
        views=definitions.views,
        scalar_functions=definitions.scalar_functions,
    )


async def run(
    initialized: asyncio.Event | None = None,
    monitor: HealthMonitor | None = None,
    *,
    lane: CatalogueLaneReporter | None = None,
    lane_index: int = 0,
    lane_count: int = 1,
    definition_provider: Callable[
        [int, int], list[SimpleNamespace]
    ] | None = None,
    definitions_ready: asyncio.Event | None = None,
    bootstrap_semaphore: asyncio.Semaphore | None = None,
) -> None:
    lane = lane or CatalogueLaneReporter(lane_index=lane_index)
    if monitor is not None:
        lane.attach(lambda: monitor.status(include_liveness=False))
    client = await connect_nats()
    jetstream = client.jetstream()
    await ensure_catalogue_event_stream(jetstream)
    leases = await ensure_operation_lease_storage(jetstream)
    if lane_index == 0:
        await reconcile_materialization_consumers(jetstream)
    ddl_subscription = (
        await jetstream.pull_subscribe(
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
        if lane_index == 0
        else None
    )
    catalogue = await asyncio.to_thread(
        catalogue_from_env,
        memory_limit=materialization_duckdb_memory_limit(),
    )
    subscriptions: dict[UUID, object] = {}
    pulls: dict[UUID, asyncio.Task] = {}
    ddl_pull: asyncio.Task | None = None
    stop = asyncio.Event()
    active_operation_count = lane
    if monitor is not None:
        monitor.dependencies_ready()
        monitor.subsystem_ready("materializations")
    if initialized is not None:
        initialized.set()
    try:
        if definitions_ready is not None:
            await definitions_ready.wait()
        while True:
            worked = False
            if ddl_pull is not None and ddl_pull.done():
                try:
                    ddl_messages = ddl_pull.result()
                except (NatsTimeoutError, TimeoutError):
                    ddl_messages = []
                ddl_pull = None
                worked = await _apply_ddl_messages(ddl_messages) or worked
            if ddl_subscription is not None and ddl_pull is None:
                ddl_pull = asyncio.create_task(
                    ddl_subscription.fetch(batch=100, timeout=60),
                    name="materialization-ddl-pull",
                )
            definitions = (
                definition_provider(lane_index, lane_count)
                if definition_provider is not None
                else await asyncio.to_thread(
                    _active_definitions,
                    lane_index=lane_index,
                    lane_count=lane_count,
                )
            )
            active_ids = {item.id for item in definitions}
            for materialization_id in set(pulls).difference(active_ids):
                pulls.pop(materialization_id).cancel()
            subscriptions = {
                key: value for key, value in subscriptions.items() if key in active_ids
            }
            for definition in definitions:
                try:
                    if definition.desired_state == "deleting":
                        pull = pulls.pop(definition.id, None)
                        if pull is not None:
                            pull.cancel()
                        await _tracked_operation(
                            active_operation_count,
                            _delete_one(
                                jetstream,
                                catalogue,
                                leases,
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
                        pull = pulls.pop(definition.id, None)
                        if pull is not None:
                            pull.cancel()
                        worked = (
                            await _run_bootstrap(
                                active_operation_count,
                                bootstrap_semaphore,
                                catalogue,
                                leases,
                                definition.id,
                            )
                            or worked
                        )
                        continue
                    if definition.observed_state == "backfilling":
                        pull = pulls.get(definition.id)
                        if pull is not None and pull.done():
                            pulls.pop(definition.id)
                            try:
                                messages = pull.result()
                            except (NatsTimeoutError, TimeoutError):
                                messages = []
                            if messages:
                                worked = (
                                    await _refresh_messages(
                                        active_operation_count,
                                        catalogue,
                                        leases,
                                        subscription,
                                        definition,
                                        messages,
                                    )
                                    or worked
                                )
                                continue
                        if definition.id not in pulls:
                            pulls[definition.id] = asyncio.create_task(
                                subscription.fetch(batch=100, timeout=60),
                                name=f"materialization-{definition.id}-pull",
                            )
                        worked = (
                            await _run_bootstrap(
                                active_operation_count,
                                bootstrap_semaphore,
                                catalogue,
                                leases,
                                definition.id,
                            )
                            or worked
                        )
                        continue
                    if definition.desired_state == "paused":
                        pull = pulls.pop(definition.id, None)
                        if pull is not None:
                            pull.cancel()
                        await asyncio.to_thread(
                            _mark_observed, definition.id, "paused"
                        )
                        continue
                    if definition.observed_state == "paused":
                        await asyncio.to_thread(
                            _mark_observed, definition.id, "live"
                        )
                        worked = True
                        continue
                    if definition.observed_state in {"blocked_schema", "failed"}:
                        pull = pulls.pop(definition.id, None)
                        if pull is not None:
                            pull.cancel()
                        continue
                    pull = pulls.get(definition.id)
                    if pull is not None and pull.done():
                        pulls.pop(definition.id)
                        try:
                            messages = pull.result()
                        except (NatsTimeoutError, TimeoutError):
                            messages = []
                        if messages:
                            worked = (
                                await _refresh_messages(
                                    active_operation_count,
                                    catalogue,
                                    leases,
                                    subscription,
                                    definition,
                                    messages,
                                )
                                or worked
                            )
                    if definition.id not in pulls:
                        pulls[definition.id] = asyncio.create_task(
                            subscription.fetch(batch=100, timeout=60),
                            name=f"materialization-{definition.id}-pull",
                        )
                except OperationLeaseUnavailable:
                    continue
                except OperationLeaseLost:
                    logging.warning(
                        "materialization %s operation lease lost; "
                        "retrying from its Postgres checkpoint",
                        definition.id,
                    )
                    continue
                except MaterializationControlChanged:
                    continue
                except CatalogueCompilerSnapshotChanged:
                    logging.warning(
                        "materialization %s compiler metadata changed; "
                        "retrying without failing the incarnation",
                        definition.id,
                    )
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if is_retryable_catalogue_transaction_conflict(exc):
                        logging.warning(
                            "materialization %s transaction conflicted after "
                            "bounded retries; continuing from its Postgres checkpoint",
                            definition.id,
                            exc_info=True,
                        )
                        continue
                    logging.exception(
                        "materialization %s failed", definition.id
                    )
                    await asyncio.to_thread(_mark_failed, definition.id, exc)
            if not worked:
                waiters = set(pulls.values())
                if ddl_pull is not None:
                    waiters.add(ddl_pull)
                if waiters:
                    await asyncio.wait(
                        waiters,
                        timeout=get_float(
                            "ATLAS_MATERIALIZATION_CONTROL_POLL_SECONDS"
                        ),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                else:
                    await asyncio.sleep(
                        get_float(
                            "ATLAS_MATERIALIZATION_CONTROL_POLL_SECONDS"
                        )
                    )
    finally:
        stop.set()
        if ddl_pull is not None:
            ddl_pull.cancel()
        for pull in pulls.values():
            pull.cancel()
        await asyncio.gather(
            *((ddl_pull,) if ddl_pull is not None else ()),
            *pulls.values(),
            return_exceptions=True,
        )
        await asyncio.to_thread(catalogue.close)
        await client.close()


def _active_definitions(
    *, lane_index: int = 0, lane_count: int = 1
) -> list[SimpleNamespace]:
    if lane_count <= 0 or lane_index < 0 or lane_index >= lane_count:
        raise ValueError("invalid materialization lane")
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
                refresh_strategy=row.refresh_strategy,
                key_columns=tuple(row.key_columns),
                source_table_id=row.source_table_id,
                bootstrap_snapshot=row.bootstrap_snapshot,
                bootstrap_partition_count=row.bootstrap_partition_count,
                bootstrap_partition_cursor=row.bootstrap_partition_cursor,
                processed_snapshot=row.processed_snapshot,
                ducklake_table_uuid=row.ducklake_table_uuid,
            )
            for row in rows
            if row.id.int % lane_count == lane_index
        ]


async def _apply_ddl_messages(messages) -> bool:
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


async def reconcile_materialization_consumers(jetstream) -> tuple[str, ...]:
    """Delete durable consumers whose Postgres incarnation is no longer active."""

    definitions = await asyncio.to_thread(
        _active_definitions, lane_index=0, lane_count=1
    )
    active = {definition.nats_consumer_name for definition in definitions}
    try:
        consumers = await jetstream.consumers_info(EVENT_STREAM)
    except NotFoundError:
        return ()
    deleted: list[str] = []
    for info in consumers:
        durable = info.name or info.config.durable_name
        subject = info.config.filter_subject or ""
        if (
            not durable.startswith(MATERIALIZATION_DURABLE_PREFIX)
            or not subject.startswith(f"{DML_SUBJECT_PREFIX}.")
            or durable in active
        ):
            continue
        try:
            await jetstream.delete_consumer(EVENT_STREAM, durable)
        except NotFoundError:
            continue
        deleted.append(durable)
    if deleted:
        logging.info(
            "deleted %d orphaned materialization consumers: %s",
            len(deleted),
            ", ".join(deleted),
        )
    return tuple(deleted)


async def _bootstrap_one(
    catalogue, leases, materialization_id: UUID
) -> bool:
    async with operation_leases(
        leases,
        (str(materialization_id),),
        phase="materialization",
        acquire_timeout=0,
    ):
        await asyncio.to_thread(
            run_with_catalogue_retry,
            lambda: _bootstrap_materialization(catalogue, materialization_id),
            description=f"materialization {materialization_id} bootstrap",
        )
    return True


async def _tracked_operation(counter: CatalogueLaneReporter, operation):
    counter.active_operation_count += 1
    try:
        return await operation
    finally:
        counter.active_operation_count -= 1


async def _run_bootstrap(
    counter: CatalogueLaneReporter,
    semaphore: asyncio.Semaphore | None,
    catalogue,
    leases,
    materialization_id: UUID,
) -> bool:
    if semaphore is None:
        return await _tracked_operation(
            counter,
            _bootstrap_one(catalogue, leases, materialization_id),
        )
    async with semaphore:
        return await _tracked_operation(
            counter,
            _bootstrap_one(catalogue, leases, materialization_id),
        )


def _bootstrap_materialization(catalogue, materialization_id: UUID) -> None:
    plan = _load_bootstrap_plan(materialization_id)
    if plan is None:
        return
    if plan.model["observed_state"] == "backfilling":
        _backfill_materialization(catalogue, plan)
        return
    model = SimpleNamespace(**plan.model)
    reference = SimpleNamespace(**plan.reference)
    view_store = CatalogueViewStore(catalogue)
    physical_name = physical_materialization_name(model.id)
    # Recover a process death between physical creation and the Postgres update.
    try:
        _materialization_store(catalogue).table_identity(
            physical_name,
            schema_name="_atlas_materializations",
        )
    except MaterializationError:
        present = False
    else:
        present = True
    _source_view(view_store, reference, model)
    resolved_source_view_uuid = model.source_view_uuid
    store = _materialization_store(catalogue)
    if present:
        table = store.inspect(physical_name)
        source_snapshot = catalogue.latest_snapshot()
        if source_snapshot is None:
            raise MaterializationError("DuckLake has no source snapshot.")
    elif model.refresh_strategy in {"keyed", "append"}:
        table, source_snapshot = store.create_empty(
            name=physical_name,
            sql=model.source_sql,
        )
    else:
        table, source_snapshot = store.create_full(
            name=physical_name,
            sql=model.source_sql,
            append_key_columns=(),
        )
    if model.partition_column:
        table = store.set_daily_partition(
            name=physical_name, column=model.partition_column
        )
    batched = model.refresh_strategy in {"keyed", "append"}
    partition_count = (
        store.bootstrap_partition_count(
            source_table=model.source_table,
            key_columns=tuple(model.key_columns),
        )
        if batched
        else None
    )
    wrapper = (
        None
        if batched
        else view_store.replace(
            current_uuid=reference.ducklake_view_uuid,
            sql=_backing_view_sql(catalogue, physical_name),
        )
    )
    bootstrap_snapshot = catalogue.latest_snapshot() or source_snapshot

    with session_scope() as session:
        model = session.get(CatalogueMaterialization, materialization_id)
        if (
            model is None
            or model.archived_at is not None
            or model.observed_state != "creating"
            or model.desired_state == "deleting"
            or model.view_reference_id != plan.model["view_reference_id"]
            or model.source_sql != plan.model["source_sql"]
            or model.refresh_strategy != plan.model["refresh_strategy"]
            or tuple(model.key_columns) != tuple(plan.model["key_columns"])
            or model.partition_column != plan.model["partition_column"]
        ):
            raise MaterializationControlChanged(
                f"Materialization {materialization_id} changed during bootstrap."
            )
        reference = session.get(CatalogueViewReference, model.view_reference_id)
        if (
            reference is None
            or reference.ducklake_view_uuid
            != plan.reference["ducklake_view_uuid"]
        ):
            raise MaterializationControlChanged(
                f"Materialization {materialization_id} view changed during bootstrap."
            )
        if wrapper is not None:
            reference.ducklake_view_uuid = wrapper.view_uuid
        model.source_view_uuid = resolved_source_view_uuid
        model.target_table_id = table.table_id
        model.ducklake_table_uuid = table.table_uuid
        model.bootstrap_snapshot = bootstrap_snapshot
        model.processed_snapshot = source_snapshot
        model.bootstrap_partition_count = partition_count
        model.bootstrap_partition_cursor = 0 if batched else None
        model.observed_state = (
            "backfilling"
            if batched
            else ("live" if model.desired_state == "live" else "paused")
        )
        model.last_refreshed_at = datetime.now(UTC)
        model.last_error = None
        session.flush()


def _backfill_materialization(catalogue, plan: SimpleNamespace) -> None:
    model = SimpleNamespace(**plan.model)
    if (
        model.ducklake_table_uuid is None
        or model.bootstrap_partition_count is None
        or model.bootstrap_partition_cursor is None
    ):
        raise MaterializationError("Batched bootstrap state is incomplete.")
    store = _materialization_store(catalogue)
    physical_name = physical_materialization_name(model.id)
    if model.bootstrap_partition_cursor < model.bootstrap_partition_count:
        kwargs = {
            "name": physical_name,
            "expected_uuid": model.ducklake_table_uuid,
            "sql": model.source_sql,
            "source_table": model.source_table,
            "key_columns": tuple(model.key_columns),
            "partition": model.bootstrap_partition_cursor,
            "partition_count": model.bootstrap_partition_count,
        }
        if model.refresh_strategy == "keyed":
            store.backfill_keyed_partition(**kwargs)
        elif model.refresh_strategy == "append":
            store.backfill_append_partition(**kwargs)
        else:
            raise MaterializationError(
                "Only keyed and append materializations use batched bootstrap."
            )
        with session_scope() as session:
            current = session.get(CatalogueMaterialization, model.id)
            if (
                current is None
                or current.archived_at is not None
                or current.observed_state != "backfilling"
                or current.ducklake_table_uuid != model.ducklake_table_uuid
                or current.bootstrap_partition_count
                != model.bootstrap_partition_count
                or current.bootstrap_partition_cursor
                != model.bootstrap_partition_cursor
            ):
                raise MaterializationControlChanged(
                    f"Materialization {model.id} changed during backfill."
                )
            current.bootstrap_partition_cursor += 1
            current.last_refreshed_at = datetime.now(UTC)
        return

    view_store = CatalogueViewStore(catalogue)
    reference = SimpleNamespace(**plan.reference)
    wrapper = view_store.replace(
        current_uuid=reference.ducklake_view_uuid,
        sql=_backing_view_sql(catalogue, physical_name),
    )
    with session_scope() as session:
        current = session.get(CatalogueMaterialization, model.id)
        if (
            current is None
            or current.archived_at is not None
            or current.observed_state != "backfilling"
            or current.ducklake_table_uuid != model.ducklake_table_uuid
            or current.bootstrap_partition_cursor
            != current.bootstrap_partition_count
        ):
            raise MaterializationControlChanged(
                f"Materialization {model.id} changed before publication."
            )
        current_reference = session.get(
            CatalogueViewReference, current.view_reference_id
        )
        if (
            current_reference is None
            or current_reference.ducklake_view_uuid
            != plan.reference["ducklake_view_uuid"]
        ):
            raise MaterializationControlChanged(
                f"Materialization {model.id} view changed before publication."
            )
        current_reference.ducklake_view_uuid = wrapper.view_uuid
        current.observed_state = (
            "live" if current.desired_state == "live" else "paused"
        )
        current.last_refreshed_at = datetime.now(UTC)
        current.last_error = None


def _load_bootstrap_plan(
    materialization_id: UUID,
) -> SimpleNamespace | None:
    with session_scope() as session:
        model = session.get(CatalogueMaterialization, materialization_id)
        if (
            model is None
            or model.archived_at is not None
            or model.observed_state not in {"creating", "backfilling"}
            or model.desired_state == "deleting"
        ):
            return None
        reference = session.get(
            CatalogueViewReference, model.view_reference_id
        )
        if reference is None:
            raise RuntimeError(
                "Materialization source view reference is missing."
            )
        return SimpleNamespace(
            model={
                "id": model.id,
                "view_reference_id": model.view_reference_id,
                "observed_state": model.observed_state,
                "source_sql": model.source_sql,
                "source_table": model.source_table,
                "refresh_strategy": model.refresh_strategy,
                "key_columns": tuple(model.key_columns),
                "partition_column": model.partition_column,
                "ducklake_table_uuid": model.ducklake_table_uuid,
                "bootstrap_partition_count": model.bootstrap_partition_count,
                "bootstrap_partition_cursor": model.bootstrap_partition_cursor,
                "source_view_uuid": model.source_view_uuid,
                "desired_state": model.desired_state,
            },
            reference={
                "ducklake_view_uuid": reference.ducklake_view_uuid,
                "view_name": reference.view_name,
            },
        )


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


async def _refresh_messages(
    active_operation_count,
    catalogue,
    leases,
    subscription,
    definition,
    messages,
) -> bool:
    """Acquire execution ownership only after a blocking pull returns work."""

    try:
        async with operation_leases(
            leases,
            (str(definition.id),),
            phase="materialization",
            acquire_timeout=0,
        ):
            worked = await _tracked_operation(
                active_operation_count,
                _apply_ticks(
                    catalogue,
                    subscription,
                    definition,
                    messages,
                ),
            )
        for message in messages:
            await message.ack()
        return worked
    except (OperationLeaseUnavailable, OperationLeaseLost):
        for message in messages:
            await message.nak(delay=1)
        return False
    except MaterializationControlChanged:
        for message in messages:
            await message.nak(delay=1)
        return False
    except Exception as exc:
        if not is_retryable_catalogue_transaction_conflict(exc):
            raise
        logging.warning(
            "materialization %s refresh transaction conflicted after bounded "
            "retries; returning ticks for redelivery",
            definition.id,
            exc_info=True,
        )
        for message in messages:
            await message.nak(delay=1)
        return False


async def _apply_ticks(
    catalogue,
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
        return True
    # DuckLake schema versions are catalogue-wide, so unrelated DDL can
    # advance tick.schema_version without changing this materialization's
    # driving table. Source/target table shape changes are fenced by the
    # global DDL relay and the materialization DDL reconciler, which matches
    # stable table IDs before marking an incarnation blocked.
    await asyncio.to_thread(
        run_with_catalogue_retry,
        lambda: _refresh_materialization(
            catalogue,
            definition.id,
            min(tick.snapshot_id for tick in fresh),
            max(tick.snapshot_id for tick in fresh),
        ),
        description=f"materialization {definition.id} refresh",
    )
    return True


def _refresh_materialization(
    catalogue,
    materialization_id: UUID,
    from_snapshot: int,
    processed_snapshot: int,
) -> None:
    plan = _load_refresh_plan(materialization_id)
    if plan is None:
        raise MaterializationControlChanged(
            f"Materialization {materialization_id} is no longer live."
        )
    store = _materialization_store(catalogue)
    physical_name = physical_materialization_name(plan.id)
    if plan.refresh_strategy == "full":
        store.refresh_full(
            name=physical_name,
            expected_uuid=plan.ducklake_table_uuid,
            sql=plan.source_sql,
        )
    elif plan.refresh_strategy == "keyed":
        store.refresh_keyed(
            name=physical_name,
            expected_uuid=plan.ducklake_table_uuid,
            sql=plan.source_sql,
            source_table=plan.source_table,
            source_table_id=plan.source_table_id,
            from_snapshot=from_snapshot,
            to_snapshot=processed_snapshot,
            key_columns=plan.key_columns,
        )
    elif plan.refresh_strategy == "append":
        store.refresh_append(
            name=physical_name,
            expected_uuid=plan.ducklake_table_uuid,
            sql=plan.source_sql,
            source_table=plan.source_table,
            source_table_id=plan.source_table_id,
            from_snapshot=from_snapshot,
            to_snapshot=processed_snapshot,
            key_columns=plan.key_columns,
        )
    else:
        raise RuntimeError(
            f"Unknown materialization refresh strategy {plan.refresh_strategy!r}."
        )
    with session_scope() as session:
        model = session.get(CatalogueMaterialization, materialization_id)
        if (
            model is None
            or model.archived_at is not None
            or model.desired_state != "live"
            or model.ducklake_table_uuid != plan.ducklake_table_uuid
            or model.processed_snapshot != plan.processed_snapshot
            or model.refresh_strategy != plan.refresh_strategy
            or model.source_sql != plan.source_sql
            or getattr(model, "source_table_id", None) != plan.source_table_id
            or tuple(getattr(model, "key_columns", ())) != plan.key_columns
        ):
            raise MaterializationControlChanged(
                f"Materialization {materialization_id} changed during refresh."
            )
        model.processed_snapshot = processed_snapshot
        model.last_refreshed_at = datetime.now(UTC)
        if model.observed_state != "backfilling":
            model.observed_state = "live"
        model.last_error = None
        session.flush()


def _load_refresh_plan(materialization_id: UUID) -> SimpleNamespace | None:
    with session_scope() as session:
        model = session.get(CatalogueMaterialization, materialization_id)
        if (
            model is None
            or model.archived_at is not None
            or model.desired_state != "live"
            or model.ducklake_table_uuid is None
        ):
            return None
        return SimpleNamespace(
            id=model.id,
            ducklake_table_uuid=model.ducklake_table_uuid,
            processed_snapshot=model.processed_snapshot,
            refresh_strategy=model.refresh_strategy,
            source_sql=model.source_sql,
            source_table=getattr(model, "source_table", None),
            source_table_id=getattr(model, "source_table_id", None),
            key_columns=tuple(getattr(model, "key_columns", ())),
        )

async def _delete_one(
    jetstream, catalogue, leases, definition
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
        await asyncio.to_thread(
            dematerialize_one, catalogue, definition.id
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
