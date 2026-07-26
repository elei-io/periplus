"""Basin CDC ingress worker for Atlas catalogue events."""

from __future__ import annotations

import asyncio

from catalogue_relay.executor import run as run_relay
from config import get_float
from repository.ingestion.health import HealthMonitor
from workers.lifecycle import run_worker_process


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_CATALOGUE_RELAY_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    await run_worker_process(
        role="catalogue_relay",
        monitor=monitor,
        tasks={"catalogue-relay": run_relay(monitor=monitor)},
        stop=stop,
    )
