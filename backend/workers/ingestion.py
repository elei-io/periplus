"""Atlas ingestion and graph-navigation worker."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from config import get_float, get_str
from repository.ingestion.health import HealthMonitor
from repository.ingestion.worker import run as run_ingestion
from runtime.catalog_navigation import run as run_navigation


async def run() -> None:
    stop = asyncio.Event()
    initialized = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_INGESTION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    loop = asyncio.get_running_loop()
    for value in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(value, stop.set)
    tasks = [
        asyncio.create_task(
            run_ingestion(initialized, monitor), name="ingestion-writer"
        ),
        asyncio.create_task(
            _run_navigation_after_initialization(initialized, monitor),
            name="ingestion-navigation",
        ),
    ]
    stop_task = asyncio.create_task(stop.wait(), name="ingestion-stop")
    done, _pending = await asyncio.wait(
        [*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED
    )
    error = next(
        (
            task.exception()
            for task in done
            if task is not stop_task and not task.cancelled()
        ),
        None,
    )
    for task in (*tasks, stop_task):
        task.cancel()
    await asyncio.gather(*tasks, stop_task, return_exceptions=True)
    if error is not None:
        raise error


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


def main() -> None:
    argparse.ArgumentParser(description="Run the Atlas ingestion worker.").parse_args()
    logging.basicConfig(
        level=get_str("ATLAS_LOG_LEVEL"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
