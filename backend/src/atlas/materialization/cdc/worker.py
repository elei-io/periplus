"""Basin-to-Atlas CDC worker process."""

from __future__ import annotations

import asyncio

from atlas.materialization.cdc.relay import run as run_cdc
from atlas.platform.config import get_float
from atlas.platform.health import HealthMonitor
from atlas.platform.process import run_worker_process


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
