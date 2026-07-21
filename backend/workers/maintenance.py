"""Atlas off-path catalogue maintenance worker."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from config import get_float
from nats.errors import TimeoutError as NatsTimeoutError
from repository.ingestion.health import HealthMonitor
from repository.maintenance import MaintenanceConfig, cleanup_staging, compact
from runtime.catalogue_lane import run_catalogue_operation
from runtime.catalogue_events import (
    DML_ALL_SUBJECT,
    EVENT_STREAM,
    MAINTENANCE_WAKE_DURABLE,
    CatalogueDMLTick,
    ensure_catalogue_event_stream,
    maintenance_wake_consumer_config,
)
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
from workers.lifecycle import cancel_task, run_worker_process


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


async def _watch_compaction_ticks(
    stop: asyncio.Event,
    wake: asyncio.Event,
    subscription,
    monitor: HealthMonitor | None = None,
) -> None:
    """Turn durable catalogue ticks into coalesced, recoverable wake-up hints."""

    while not stop.is_set():
        try:
            messages = await subscription.fetch(batch=100, timeout=1)
        except NatsTimeoutError:
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if monitor is not None:
                monitor.subsystem_unavailable(
                    "maintenance_catalogue_ticks",
                    str(exc) or type(exc).__name__,
                )
            if not stop.is_set():
                logging.exception(
                    "catalogue tick wake-up unavailable; periodic fallback remains active"
                )
                await _wait(stop, 5)
            continue

        valid_messages = []
        for message in messages:
            try:
                CatalogueDMLTick.model_validate_json(message.data)
            except Exception:
                logging.exception("discarding invalid catalogue maintenance tick")
                try:
                    await message.term()
                except Exception:
                    logging.exception("failed to terminate invalid catalogue tick")
                continue
            valid_messages.append(message)
        if not valid_messages:
            continue

        # These are hints rather than work items. Record the local debt before
        # acknowledging; the periodic metadata sweep recovers a process death
        # immediately after acknowledgement.
        wake.set()
        for message in valid_messages:
            await message.ack()
        if monitor is not None:
            monitor.subsystem_ready("maintenance_catalogue_ticks")


async def _run(stop: asyncio.Event, monitor: HealthMonitor) -> None:
    config = MaintenanceConfig.from_env()
    client = await connect_nats()
    jetstream = client.jetstream()
    await ensure_catalogue_event_stream(jetstream)
    operation_lease_store = await ensure_operation_lease_storage(jetstream)
    resource_grants = await ensure_resource_governor_storage(jetstream)
    tick_subscription = await jetstream.pull_subscribe(
        DML_ALL_SUBJECT,
        durable=MAINTENANCE_WAKE_DURABLE,
        stream=EVENT_STREAM,
        config=maintenance_wake_consumer_config(),
    )
    monitor.dependencies_ready()
    monitor.subsystem_ready("maintenance_admission")
    monitor.subsystem_ready("maintenance_catalogue_ticks")
    compaction_wake = asyncio.Event()
    compaction_wake.set()
    tick_task = asyncio.create_task(
        _watch_compaction_ticks(
            stop,
            compaction_wake,
            tick_subscription,
            monitor,
        )
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
        await cancel_task(tick_task)
        await client.drain()


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_MAINTENANCE_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    await run_worker_process(
        role="maintenance",
        monitor=monitor,
        tasks={"maintenance": _run(stop, monitor)},
        stop=stop,
    )
