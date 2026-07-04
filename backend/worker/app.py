from __future__ import annotations

import asyncio
import os
import signal

from sqlalchemy.exc import SQLAlchemyError

from db import SessionLocal
from tasks.executor import run_worker_once
from tasks.scheduler import run_scheduler_once


async def _loop() -> None:
    poll_seconds = float(os.getenv("ATLAS_WORKER_POLL_SECONDS", "5"))
    scheduler_limit = int(os.getenv("ATLAS_SCHEDULER_BATCH_SIZE", "20"))
    worker_id = os.getenv("ATLAS_WORKER_ID") or f"{os.uname().nodename}:{os.getpid()}"
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


def main() -> None:
    asyncio.run(_loop())


if __name__ == "__main__":
    main()
