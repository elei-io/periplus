"""Atlas live-materialization worker."""

from __future__ import annotations

import asyncio

from config import get_float
from materialization.executor import run as run_executor
from repository.ingestion.health import HealthMonitor
from workers.lifecycle import install_signal_handlers, supervise_until_stopped


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_MATERIALIZATION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    install_signal_handlers(stop)
    await supervise_until_stopped(
        {"materialization-scopes": run_executor(monitor=monitor)},
        stop,
    )
