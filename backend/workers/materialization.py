"""Atlas live-materialization worker."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from config import get_float, get_str
from materialization.executor import run as run_executor
from repository.ingestion.health import HealthMonitor


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_MATERIALIZATION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    loop = asyncio.get_running_loop()
    for value in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(value, stop.set)
    tasks = [
        asyncio.create_task(
            run_executor(monitor=monitor), name="materialization-scopes"
        )
    ]
    stop_task = asyncio.create_task(stop.wait(), name="materialization-stop")
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


def main() -> None:
    argparse.ArgumentParser(
        description="Run the Atlas live-materialization worker."
    ).parse_args()
    logging.basicConfig(
        level=get_str("ATLAS_LOG_LEVEL"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
