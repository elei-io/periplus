"""Atlas catalogue-ingestion worker."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os

from atlas.platform.config import get_float, get_int
from atlas.platform.config.environment import ConfigurationError
from atlas.platform.config.performance import (
    INGESTION_MAX_LOCAL_CONCURRENCY,
)
from atlas.platform.health import HealthMonitor
from atlas.ingestion.consumer import run as run_ingestion
from atlas.ingestion.queue import (
    DURABLE,
    STREAM,
    SUBJECT,
    ensure_dead_letter_stream,
    ensure_ingestion_results,
    ensure_repository_consumer,
    ensure_repository_stream,
)
from atlas.platform.messaging.catalogue_workers import (
    CatalogueLaneReporter,
    monitor_catalogue_lanes,
    run_catalogue_process_presence,
)
from atlas.platform.messaging.client import connect_nats
from atlas.platform.messaging.leases import ensure_operation_lease_storage
from atlas.platform.process import run_worker_process


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_INGESTION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    concurrency = _ingestion_concurrency()
    lanes = tuple(
        CatalogueLaneReporter(lane_index=lane)
        for lane in range(concurrency)
    )
    client = await connect_nats()
    try:
        jetstream = client.jetstream()
        await ensure_repository_stream(jetstream)
        await ensure_dead_letter_stream(jetstream)
        results_store = await ensure_ingestion_results(jetstream)
        leases = await ensure_operation_lease_storage(jetstream)
        await ensure_repository_consumer(jetstream)
        subscriptions = [
            await jetstream.pull_subscribe(
                SUBJECT,
                durable=DURABLE,
                stream=STREAM,
            )
            for _lane in range(concurrency)
        ]
        monitor.dependencies_ready()
        await run_worker_process(
            role="ingestion",
            monitor=monitor,
            tasks={
                f"ingestion-writer-{lane}": run_ingestion(
                    stop=stop,
                    monitor=HealthMonitor(),
                    jetstream=jetstream,
                    results_store=results_store,
                    leases=leases,
                    subscription=subscriptions[lane],
                    lane=lanes[lane],
                    lane_index=lane,
                )
                for lane in range(concurrency)
            }
            | {
                "ingestion-presence": run_catalogue_process_presence(
                    worker_id=(
                        f"ingestion:{os.uname().nodename}:{os.getpid()}"
                    ),
                    capability="ingestion",
                    started_at=datetime.now(UTC),
                    lane_reporters=lanes,
                    process_health=lambda: monitor.status(
                        exclude_subsystems=frozenset(
                            {"catalogue_worker_presence"}
                        )
                    ),
                    stop=stop,
                    monitor=monitor,
                ),
                "ingestion-lane-health": monitor_catalogue_lanes(
                    lane_reporters=lanes,
                    stop=stop,
                    monitor=monitor,
                ),
            },
            stop=stop,
        )
    finally:
        await client.drain()


def _ingestion_concurrency() -> int:
    concurrency = get_int("ATLAS_INGESTION_CONCURRENCY")
    if concurrency > INGESTION_MAX_LOCAL_CONCURRENCY:
        raise ConfigurationError(
            "ATLAS_INGESTION_CONCURRENCY must be at most "
            f"{INGESTION_MAX_LOCAL_CONCURRENCY}; add replicas to scale further"
        )
    return concurrency
