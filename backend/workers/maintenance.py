"""Atlas off-path catalogue maintenance worker."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from ducklake_cdc_client import DMLConsumer

from config import get_float
from materialization.definitions import active_definitions

from repository.catalogue import Catalogue, catalogue_from_env
from repository.catalogue.cdc import validate_cdc_extension
from repository.ingestion.health import HealthMonitor
from repository.maintenance import MaintenanceConfig, cleanup_staging, compact
from runtime.catalogue_lane import run_catalogue_operation
from runtime.nats_client import connect_nats
from runtime.operation_leases import (
    OperationLeaseLost,
    OperationLeaseUnavailable,
    ensure_operation_lease_storage,
    operation_leases,
)
from runtime.resource_governor import (
    DURABLE_RESOURCE_WAIT,
    ResourceCapacityUnavailable,
    ResourceLimits,
    ResourcePermitLost,
    catalogue_request,
    ensure_resource_governor_storage,
    resource_permits,
)
from workers.lifecycle import (
    WorkerEndpointConfig,
    WorkerEndpoints,
    cancel_task,
    install_signal_handlers,
    monitor_heartbeat,
)


MaintenanceKind = Literal["compact", "cleanup"]
MaintenanceOutcome = Literal["worked", "idle", "deferred", "failed"]


async def _run_operation(
    *,
    kind: MaintenanceKind,
    operation_lease_store,
    resource_grants,
    config: MaintenanceConfig,
    monitor: HealthMonitor | None = None,
) -> MaintenanceOutcome:
    """Run one singleton operation only after all shared pressure has drained."""

    limits = ResourceLimits.from_env()

    def operation() -> bool:
        if kind == "compact":
            return any(
                result.files_processed > 0
                for result in compact(config)
            )
        cleanup_staging(config)
        return False

    try:
        async with operation_leases(
            operation_lease_store,
            (kind,),
            phase="maintenance",
            acquire_timeout=0,
        ):
            async with resource_permits(
                resource_grants,
                catalogue_request(
                    f"maintenance:{kind}",
                    service_class="maintenance",
                    object_read_units=limits.object_read,
                    object_write_units=limits.object_write,
                    limits=limits,
                    exclusive=True,
                ),
                acquire_timeout=DURABLE_RESOURCE_WAIT,
            ):
                worked = await run_catalogue_operation(operation)
        if monitor is not None:
            monitor.subsystem_ready("maintenance_admission")
        return "worked" if worked else "idle"
    except (
        OperationLeaseUnavailable,
        ResourceCapacityUnavailable,
    ):
        return "deferred"
    except (OperationLeaseLost, ResourcePermitLost) as exc:
        if monitor is not None:
            monitor.subsystem_unavailable("maintenance_admission", str(exc))
        logging.exception("maintenance %s admission was lost", kind)
        return "deferred"
    except Exception:
        logging.exception("maintenance operation %s failed", kind)
        return "failed"


async def _wait(
    stop: asyncio.Event,
    seconds: float,
) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def _wait_for_signal(
    stop: asyncio.Event,
    wake: asyncio.Event,
    *,
    timeout: float,
) -> Literal["stop", "wake", "timeout"]:
    stop_task = asyncio.create_task(stop.wait())
    wake_task = asyncio.create_task(wake.wait())
    try:
        done, _pending = await asyncio.wait(
            (stop_task, wake_task),
            timeout=max(0.0, timeout),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done and stop_task.result():
            return "stop"
        if wake_task in done and wake_task.result():
            return "wake"
        return "timeout"
    finally:
        for task in (stop_task, wake_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(stop_task, wake_task, return_exceptions=True)


async def _wait_for_compaction_trigger(
    stop: asyncio.Event,
    wake: asyncio.Event,
    *,
    fallback_seconds: float,
    debounce_seconds: float,
    maximum_delay_seconds: float,
) -> Literal["stop", "event", "periodic"]:
    """Coalesce commit activity while retaining a periodic safety sweep."""

    signal = await _wait_for_signal(stop, wake, timeout=fallback_seconds)
    if signal == "stop":
        return "stop"
    if signal == "timeout":
        return "periodic"

    wake.clear()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + maximum_delay_seconds
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return "event"
        signal = await _wait_for_signal(
            stop,
            wake,
            timeout=min(debounce_seconds, remaining),
        )
        if signal == "stop":
            return "stop"
        if signal == "timeout":
            return "event"
        wake.clear()


def _close_cdc_consumer(
    catalogue: Catalogue,
    consumer: DMLConsumer,
    *,
    drop: bool = False,
) -> None:
    name = consumer.name
    consumer.close(timeout=5.0, cancel=False, release=True)
    if drop:
        catalogue.connection.execute(
            "SELECT * FROM cdc_consumer_drop(?, ?)",
            [catalogue.config.alias, name],
        )


def _compaction_sources(catalogue: Catalogue) -> dict[str, str]:
    sources = {
        "atlas-compaction-wakeup": f"{catalogue.config.schema}.crawls",
        "atlas-compaction-urls": f"{catalogue.config.schema}.urls",
    }
    for definition in active_definitions():
        sources[f"atlas-compact-{definition.ducklake_table_uuid.hex}"] = (
            f"_atlas_materializations.{definition.name}"
        )
    return sources


def _open_compaction_consumer(
    catalogue: Catalogue,
    *,
    name: str,
    table: str,
    start_at: int,
    on_exists: str = "use",
) -> DMLConsumer:
    return DMLConsumer(
        catalogue.lake,
        name,
        connection=catalogue.connection,
        table=table,
        mode="ticks",
        start_at=start_at,
        on_exists=on_exists,
        lease_policy="error",
    ).open()


async def _watch_compaction_commits(
    stop: asyncio.Event,
    wake: asyncio.Event,
) -> None:
    """Wake maintenance from base and materialized-table ticks without row reads."""

    while not stop.is_set():
        catalogue = None
        consumers: dict[str, tuple[str, DMLConsumer]] = {}
        try:
            catalogue = await run_catalogue_operation(catalogue_from_env)
            await run_catalogue_operation(validate_cdc_extension, catalogue)
            reconcile_at = 0.0
            while not stop.is_set():
                now = asyncio.get_running_loop().time()
                if now >= reconcile_at:
                    sources = await asyncio.to_thread(_compaction_sources, catalogue)
                    for name in set(consumers) - set(sources):
                        _table, consumer = consumers.pop(name)
                        await run_catalogue_operation(
                            _close_cdc_consumer,
                            catalogue,
                            consumer,
                            drop=True,
                        )
                    start_at = await run_catalogue_operation(
                        catalogue.latest_snapshot
                    )
                    if start_at is not None:
                        for name, table in sources.items():
                            if name in consumers:
                                continue
                            consumer = await run_catalogue_operation(
                                _open_compaction_consumer,
                                catalogue,
                                name=name,
                                table=table,
                                start_at=start_at,
                            )
                            consumers[name] = (table, consumer)
                    reconcile_at = now + 5

                saw_commit = False
                for name, (table, consumer) in list(consumers.items()):
                    batch = await run_catalogue_operation(
                        consumer.read,
                        max_snapshots=100,
                    )
                    if batch is None:
                        window = await run_catalogue_operation(
                            consumer.window,
                            max_snapshots=100,
                        )
                        if (
                            window.terminal
                            and window.terminal_at_snapshot is not None
                        ):
                            await run_catalogue_operation(
                                _close_cdc_consumer,
                                catalogue,
                                consumer,
                                drop=True,
                            )
                            replacement = await run_catalogue_operation(
                                _open_compaction_consumer,
                                catalogue,
                                name=name,
                                table=table,
                                start_at=window.terminal_at_snapshot,
                                on_exists="error",
                            )
                            consumers[name] = (table, replacement)
                        continue
                    saw_commit = True
                    wake.set()
                    await run_catalogue_operation(batch.commit)
                if not saw_commit:
                    await _wait(stop, 1)
        except Exception:
            if not stop.is_set():
                logging.exception(
                    "compaction CDC wake-up unavailable; periodic fallback remains active"
                )
                await _wait(stop, 5)
        finally:
            if catalogue is not None:
                for _table, consumer in consumers.values():
                    try:
                        await run_catalogue_operation(
                            _close_cdc_consumer,
                            catalogue,
                            consumer,
                        )
                    except Exception:
                        logging.exception(
                            "failed to release compaction CDC consumer %s",
                            consumer.name,
                        )
                try:
                    await run_catalogue_operation(catalogue.close)
                except Exception:
                    logging.exception(
                        "failed to close compaction CDC catalogue"
                    )


async def run() -> None:
    stop = asyncio.Event()
    install_signal_handlers(stop)
    config = MaintenanceConfig.from_env()
    client = await connect_nats()
    jetstream = client.jetstream()
    operation_lease_store = await ensure_operation_lease_storage(jetstream)
    resource_grants = await ensure_resource_governor_storage(jetstream)
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_MAINTENANCE_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    monitor.dependencies_ready()
    monitor.subsystem_ready("maintenance_admission")
    endpoints = WorkerEndpoints(WorkerEndpointConfig.from_env("maintenance"))
    endpoints.start_health(monitor)
    endpoints.start_metrics()
    heartbeat_task = asyncio.create_task(monitor_heartbeat(monitor, stop))
    compaction_wake = asyncio.Event()
    compaction_wake.set()
    cdc_task = asyncio.create_task(
        _watch_compaction_commits(stop, compaction_wake)
    )
    loop = asyncio.get_running_loop()
    next_periodic = loop.time() + config.interval_seconds
    try:
        while not stop.is_set():
            trigger = await _wait_for_compaction_trigger(
                stop,
                compaction_wake,
                fallback_seconds=max(0.0, next_periodic - loop.time()),
                debounce_seconds=config.debounce_seconds,
                maximum_delay_seconds=config.maximum_delay_seconds,
            )
            if trigger == "stop":
                break
            outcome = await _run_operation(
                kind="compact",
                operation_lease_store=operation_lease_store,
                resource_grants=resource_grants,
                config=config,
                monitor=monitor,
            )
            if trigger == "periodic" or loop.time() >= next_periodic:
                await _run_operation(
                    kind="cleanup",
                    operation_lease_store=operation_lease_store,
                    resource_grants=resource_grants,
                    config=config,
                    monitor=monitor,
                )
                next_periodic = loop.time() + config.interval_seconds
            if outcome in {"worked", "deferred"}:
                await _wait(stop, config.retry_seconds)
                compaction_wake.set()
    finally:
        stop.set()
        await cancel_task(cdc_task)
        await cancel_task(heartbeat_task)
        await endpoints.close()
        await client.drain()
