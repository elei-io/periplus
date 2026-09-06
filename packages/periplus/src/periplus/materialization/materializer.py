"""Periplus materializer process."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os

from periplus.platform.config import get_float, get_int
from periplus.materialization.executor import run as run_materialization
from periplus.platform.health import HealthMonitor
from periplus.platform.messaging.catalogue_workers import (
    CatalogueLaneReporter,
    monitor_catalogue_lanes,
    run_catalogue_process_presence,
)
from periplus.platform.process import run_worker_process


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "PERIPLUS_MATERIALIZER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    concurrency = get_int("PERIPLUS_MATERIALIZER_CONCURRENCY")
    lanes = tuple(
        CatalogueLaneReporter(lane_index=index)
        for index in range(concurrency)
    )
    for reporter in lanes:
        reporter.attach(lambda: (True, "ready"))
    await run_worker_process(
        role="materializer",
        monitor=monitor,
        tasks={
            "fixed-materializations": run_materialization(
                stop=stop,
                monitor=monitor,
                concurrency=concurrency,
            ),
            "materialization-presence": run_catalogue_process_presence(
                worker_id=(
                    f"materializer:{os.uname().nodename}:{os.getpid()}"
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
