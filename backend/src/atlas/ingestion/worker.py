"""Atlas catalogue-ingestion worker."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os

from atlas.platform.config import get_float
from atlas.platform.config.performance import INGESTION_CONNECTIONS
from atlas.platform.health import HealthMonitor
from atlas.ingestion.consumer import run as run_ingestion
from atlas.platform.messaging.catalogue_workers import (
    CatalogueLaneReporter,
    monitor_catalogue_lanes,
    run_catalogue_process_presence,
)
from atlas.platform.process import run_worker_process


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_INGESTION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    monitor.dependencies_ready()
    lanes = tuple(
        CatalogueLaneReporter(lane_index=lane)
        for lane in range(INGESTION_CONNECTIONS)
    )
    await run_worker_process(
        role="ingestion",
        monitor=monitor,
        tasks={
            f"ingestion-writer-{lane}": run_ingestion(
                stop=stop,
                monitor=HealthMonitor(),
                lane=lanes[lane],
                lane_index=lane,
            )
            for lane in range(INGESTION_CONNECTIONS)
        }
        | {
            "ingestion-presence": run_catalogue_process_presence(
                worker_id=f"ingestion:{os.uname().nodename}:{os.getpid()}",
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
