"""Atlas catalogue-ingestion worker."""

from __future__ import annotations

import asyncio
from config import get_float
from config.performance import INGESTION_QUACK_CLIENTS
from repository.ingestion.health import HealthMonitor
from repository.ingestion.worker import run as run_ingestion
from workers.lifecycle import run_worker_process


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_INGESTION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    await run_worker_process(
        role="ingestion",
        monitor=monitor,
        tasks={
            f"ingestion-writer-{lane}": run_ingestion(
                stop=stop,
                monitor=monitor,
                lane_index=lane,
            )
            for lane in range(INGESTION_QUACK_CLIENTS)
        },
        stop=stop,
    )
