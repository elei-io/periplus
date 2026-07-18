"""Atlas catalogue-ingestion worker."""

from __future__ import annotations

import asyncio
from config import get_float
from repository.ingestion.health import HealthMonitor
from repository.ingestion.worker import run as run_ingestion
from workers.lifecycle import install_signal_handlers, supervise_until_stopped


async def run() -> None:
    stop = asyncio.Event()
    initialized = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_INGESTION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    install_signal_handlers(stop)
    await supervise_until_stopped(
        {"ingestion-writer": run_ingestion(initialized, monitor)},
        stop,
    )
