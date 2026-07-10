from __future__ import annotations

import argparse
import asyncio
import os
import signal
import time
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from artifacts.cleanup import artifact_cleanup_interval_seconds, run_artifact_cleanup_once
from db import SessionLocal
from tasks.executor import run_worker_once
from tasks.scheduler import run_scheduler_once


async def _loop() -> None:
    poll_seconds = float(os.getenv("ATLAS_WORKER_POLL_SECONDS", "5"))
    scheduler_limit = int(os.getenv("ATLAS_SCHEDULER_BATCH_SIZE", "20"))
    worker_id = os.getenv("ATLAS_WORKER_ID") or f"{os.uname().nodename}:{os.getpid()}"
    cleanup_interval_seconds = artifact_cleanup_interval_seconds()
    next_cleanup_at = 0.0
    stop = asyncio.Event()

    def request_stop() -> None:
        stop.set()

    running_loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        running_loop.add_signal_handler(sig, request_stop)

    print(f"atlas-worker started worker_id={worker_id}", flush=True)
    while not stop.is_set():
        try:
            enqueued = run_scheduler_once(SessionLocal, limit=scheduler_limit)
            if enqueued:
                print(f"atlas-worker enqueued={enqueued}", flush=True)

            if cleanup_interval_seconds > 0 and time.monotonic() >= next_cleanup_at:
                next_cleanup_at = time.monotonic() + cleanup_interval_seconds
                try:
                    cleanup = run_artifact_cleanup_once(SessionLocal)
                    if cleanup.changed:
                        print(
                            "atlas-worker artifact-cleanup "
                            f"invalidated={cleanup.invalidated} "
                            f"rows_deleted={cleanup.rows_deleted} "
                            f"files_deleted={cleanup.files_deleted} "
                            f"missing_files={cleanup.missing_files} "
                            f"errors={cleanup.errors}",
                            flush=True,
                        )
                except Exception as exc:
                    print(f"atlas-worker artifact-cleanup failed: {exc}", flush=True)

            claimed = await run_worker_once(SessionLocal, worker_id=worker_id)
            if claimed:
                continue
        except SQLAlchemyError as exc:
            print(f"atlas-worker database unavailable: {exc}", flush=True)

        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            pass

    print("atlas-worker stopped", flush=True)


def _run() -> None:
    asyncio.run(_loop())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Atlas task worker.")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Restart the worker when Python files change (development only).",
    )
    args = parser.parse_args()

    if args.reload:
        from watchfiles import PythonFilter, run_process

        backend_root = Path(__file__).resolve().parents[1]
        run_process(backend_root, target=_run, watch_filter=PythonFilter())
        return

    _run()


if __name__ == "__main__":
    main()
