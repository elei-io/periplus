"""Drain bounded synchronous I/O before releasing process ownership."""

import asyncio
import logging
import os
from threading import Timer
from collections.abc import Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")

WRITE_SECONDS = 300
DRAIN_SECONDS = 610


def fail_stop() -> None:
    logging.critical("Worker exceeded its bounded I/O deadline")
    os._exit(70)


async def bounded_call(
    function: Callable[P, T], *args: P.args, **kwargs: P.kwargs
) -> T:
    timer = Timer(WRITE_SECONDS, fail_stop)
    timer.daemon = True
    timer.start()
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    finally:
        if not task.done():
            await asyncio.shield(task)
        timer.cancel()
        timer.join()
