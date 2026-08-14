"""Periplus catalogue ingestor process."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os

from periplus.platform.config import get_float, get_int
from periplus.platform.config.environment import ConfigurationError
from periplus.platform.config.performance import (
    INGESTION_MAX_LOCAL_CONCURRENCY,
)
from periplus.platform.health import HealthMonitor
from periplus.ingestion.consumer import run as run_ingestion
from periplus.ingestion.queue import (
    DURABLE,
    STREAM,
    SUBJECT,
    ensure_dead_letter_stream,
    ensure_ingestion_results,
    ensure_repository_consumer,
    ensure_repository_stream,
)
from periplus.platform.messaging.catalogue_workers import (
    CatalogueLaneReporter,
    monitor_catalogue_lanes,
    run_catalogue_process_presence,
)
from periplus.platform.messaging.client import connect_nats
from periplus.platform.messaging.leases import ensure_operation_lease_storage
from periplus.platform.process import run_worker_process


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "PERIPLUS_INGESTOR_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
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
            role="ingestor",
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
                        f"ingestor:{os.uname().nodename}:{os.getpid()}"
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
    concurrency = get_int("PERIPLUS_INGESTOR_CONCURRENCY")
    if concurrency > INGESTION_MAX_LOCAL_CONCURRENCY:
        raise ConfigurationError(
            "PERIPLUS_INGESTOR_CONCURRENCY must be at most "
            f"{INGESTION_MAX_LOCAL_CONCURRENCY}; add replicas to scale further"
        )
    return concurrency
