"""Atlas catalogue hot-path worker.

One process owns ingestion, asynchronous materialization, navigation readiness, and edge
evaluation. The loops retain separate subscriptions but share one deployment and scaling unit.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from config import get_float, get_str
from materialization.executor import run as run_materializations
from repository.ingestion.worker import run as run_ingestion
from runtime.catalog_navigation import run as run_navigation
from repository.ingestion.health import HealthMonitor


async def run() -> None:
    stop = asyncio.Event()
    ingestion_initialized = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_CATALOG_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    loop = asyncio.get_running_loop()
    for value in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(value, stop.set)
    tasks = [
        asyncio.create_task(
            run_ingestion(ingestion_initialized, monitor), name="catalog-ingestion"
        ),
        asyncio.create_task(
            run_materializations(ingestion_initialized, monitor),
            name="catalog-materialization",
        ),
        asyncio.create_task(
            _supervise_navigation(stop, monitor), name="catalog-navigation"
        ),
    ]
    stop_task = asyncio.create_task(stop.wait(), name="catalog-stop")
    done, _pending = await asyncio.wait(
        [*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED
    )
    error = next(
        (task.exception() for task in done if task is not stop_task and not task.cancelled()),
        None,
    )
    for task in (*tasks, stop_task):
        task.cancel()
    await asyncio.gather(*tasks, stop_task, return_exceptions=True)
    if error is not None:
        raise error


async def _supervise_navigation(stop: asyncio.Event, monitor: HealthMonitor) -> None:
    delay = 1.0
    while not stop.is_set():
        try:
            await run_navigation(monitor=monitor)
            if not stop.is_set():
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
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(30.0, delay * 2)


def main() -> None:
    argparse.ArgumentParser(description="Run the Atlas catalog worker.").parse_args()
    logging.basicConfig(
        level=get_str("ATLAS_LOG_LEVEL"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
