"""One managed-catalogue execution lane per worker process."""

from __future__ import annotations

import asyncio

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
