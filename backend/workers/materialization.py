"""Atlas fixed CDC-driven materialization worker."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os

from config import get_float
from config.performance import MATERIALIZATION_QUACK_CLIENTS
from materialization.executor import run as run_materialization
from repository.ingestion.health import HealthMonitor
from runtime.catalogue_workers import (
    CatalogueLaneReporter,
    monitor_catalogue_lanes,
    run_catalogue_process_presence,
)
from workers.lifecycle import run_worker_process


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_MATERIALIZATION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    lanes = tuple(
        CatalogueLaneReporter(lane_index=index)
        for index in range(MATERIALIZATION_QUACK_CLIENTS)
    )
    await run_worker_process(
        role="materialization",
        monitor=monitor,
        tasks={
            "fixed-materializations": run_materialization(
                stop=stop,
                monitor=monitor,
                lanes=lanes,
            ),
            "materialization-presence": run_catalogue_process_presence(
                worker_id=(
                    f"materialization:{os.uname().nodename}:{os.getpid()}"
                ),
                capability="materialization",
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
            "materialization-lane-health": monitor_catalogue_lanes(
                lane_reporters=lanes,
                stop=stop,
                monitor=monitor,
            ),
        },
        stop=stop,
    )
