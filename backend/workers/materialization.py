"""Atlas NATS-driven materialization worker."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os
from types import SimpleNamespace

from config import get_float
from config.performance import (
    MATERIALIZATION_BOOTSTRAP_CONCURRENCY,
    MATERIALIZATION_QUACK_CLIENTS,
)
from materialization.executor import (
    _active_definitions,
    run as run_executor,
)
from repository.ingestion.health import HealthMonitor
from runtime.catalogue_workers import (
    CatalogueLaneReporter,
    monitor_catalogue_lanes,
    run_catalogue_process_presence,
)
from workers.lifecycle import run_worker_process


async def run() -> None:
    stop = asyncio.Event()
    definitions: tuple[SimpleNamespace, ...] = ()
    definitions_ready = asyncio.Event()
    bootstrap_semaphore = asyncio.Semaphore(
        MATERIALIZATION_BOOTSTRAP_CONCURRENCY
    )

    def definitions_for_lane(
        lane_index: int, lane_count: int
    ) -> list[SimpleNamespace]:
        return [
            item
            for item in definitions
            if item.id.int % lane_count == lane_index
        ]

    async def refresh_definitions() -> None:
        nonlocal definitions
        interval = get_float("ATLAS_MATERIALIZATION_CONTROL_POLL_SECONDS")
        while not stop.is_set():
            definitions = tuple(
                await asyncio.to_thread(
                    _active_definitions, lane_index=0, lane_count=1
                )
            )
            definitions_ready.set()
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass

    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_MATERIALIZATION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    monitor.dependencies_ready()
    lanes = tuple(
        CatalogueLaneReporter(lane_index=lane)
        for lane in range(MATERIALIZATION_QUACK_CLIENTS)
    )
    tasks = {
        f"materializations-{lane}": run_executor(
            monitor=HealthMonitor(),
            lane=lanes[lane],
            lane_index=lane,
            lane_count=MATERIALIZATION_QUACK_CLIENTS,
            definition_provider=definitions_for_lane,
            definitions_ready=definitions_ready,
            bootstrap_semaphore=bootstrap_semaphore,
        )
        for lane in range(MATERIALIZATION_QUACK_CLIENTS)
    }
    tasks["materialization-definitions"] = refresh_definitions()
    tasks["materialization-presence"] = run_catalogue_process_presence(
        worker_id=f"materialization:{os.uname().nodename}:{os.getpid()}",
        capability="materialization",
        started_at=datetime.now(UTC),
        lane_reporters=lanes,
        process_health=lambda: monitor.status(
            exclude_subsystems=frozenset({"catalogue_worker_presence"})
        ),
        stop=stop,
        monitor=monitor,
    )
    tasks["materialization-lane-health"] = monitor_catalogue_lanes(
        lane_reporters=lanes,
        stop=stop,
        monitor=monitor,
    )
    await run_worker_process(
        role="materialization",
        monitor=monitor,
        tasks=tasks,
        stop=stop,
    )
