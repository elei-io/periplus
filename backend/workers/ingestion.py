"""Atlas ingestion and graph-navigation worker."""

from __future__ import annotations

import asyncio
import logging

from config import get_float
from repository.ingestion.health import HealthMonitor
from repository.ingestion.worker import run as run_ingestion
from runtime.catalog_navigation import run as run_navigation
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
        {
            "ingestion-writer": run_ingestion(initialized, monitor),
            "ingestion-navigation": _run_navigation_after_initialization(
                initialized, monitor
            ),
        },
        stop,
    )


async def _run_navigation_after_initialization(
    initialized: asyncio.Event, monitor: HealthMonitor
) -> None:
    await initialized.wait()
    delay = 1.0
    while True:
        try:
            await run_navigation(monitor=monitor)
            raise RuntimeError("navigation runtime exited unexpectedly")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            monitor.subsystem_unavailable(
                "navigation", str(exc) or type(exc).__name__
            )
            logging.exception(
                "navigation runtime unavailable; retrying in %.1fs", delay
            )
            await asyncio.sleep(delay)
            delay = min(30.0, delay * 2)
