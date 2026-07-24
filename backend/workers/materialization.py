"""Atlas NATS-driven materialization worker."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from config import get_float
from config.performance import MATERIALIZATION_QUACK_CLIENTS
from materialization.executor import (
    _active_definitions,
    run as run_executor,
)
from repository.ingestion.health import HealthMonitor
from workers.lifecycle import run_worker_process


async def run() -> None:
    stop = asyncio.Event()
    definitions: tuple[SimpleNamespace, ...] = ()
    definitions_ready = asyncio.Event()

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
    tasks = {
        f"materializations-{lane}": run_executor(
            monitor=monitor,
            lane_index=lane,
            lane_count=MATERIALIZATION_QUACK_CLIENTS,
            definition_provider=definitions_for_lane,
            definitions_ready=definitions_ready,
        )
        for lane in range(MATERIALIZATION_QUACK_CLIENTS)
    }
    tasks["materialization-definitions"] = refresh_definitions()
    await run_worker_process(
        role="materialization",
        monitor=monitor,
        tasks=tasks,
        stop=stop,
    )
