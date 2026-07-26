"""Basin-to-Atlas CDC worker process."""

from __future__ import annotations

import asyncio

from cdc.relay import run as run_cdc
from config import get_float
from repository.ingestion.health import HealthMonitor
from workers.lifecycle import run_worker_process


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_CDC_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    await run_worker_process(
        role="cdc",
        monitor=monitor,
        tasks={"cdc": run_cdc(monitor=monitor)},
        stop=stop,
    )
