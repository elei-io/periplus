"""One managed-catalogue execution lane per worker process."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")

_lane: asyncio.Lock | None = None
_lane_loop: asyncio.AbstractEventLoop | None = None


def catalogue_operation_lane() -> asyncio.Lock:
    """Return the lock shared by every catalogue owner in this event loop."""

    global _lane, _lane_loop
    loop = asyncio.get_running_loop()
    if _lane is None or _lane_loop is not loop:
        _lane = asyncio.Lock()
        _lane_loop = loop
    return _lane


async def run_catalogue_operation(
    function: Callable[..., T], *args, **kwargs
) -> T:
    """Run one blocking managed-DuckDB operation in the process-owned lane."""

    async with catalogue_operation_lane():
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The connection remains owned until DuckDB has stopped using it.
            await task
            raise
