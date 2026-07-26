"""Authenticated connections to Atlas-owned and Basin-owned NATS namespaces."""

from __future__ import annotations

from typing import Any

import nats

from config import get_optional, get_str


async def connect_nats(*, connect_timeout: int = 2):
    """Connect to the Atlas namespace used by application runtimes."""

    return await _connect(
        url_name="ATLAS_NATS_URL",
        seed_name="ATLAS_NATS_SEED",
        connect_timeout=connect_timeout,
    )


async def connect_basin_nats(*, connect_timeout: int = 2):
    """Connect to the Basin namespace used only for managed lake CDC."""

    return await _connect(
        url_name="DUCKBASIN_NATS_URL",
        seed_name="DUCKBASIN_NATS_SEED",
        connect_timeout=connect_timeout,
    )


async def _connect(
    *,
    url_name: str,
    seed_name: str,
    connect_timeout: int,
):
    options: dict[str, Any] = {
        "connect_timeout": connect_timeout,
        "max_reconnect_attempts": -1,
    }
    seed = get_optional(seed_name)
    if seed is not None:
        options["nkeys_seed_str"] = seed
    return await nats.connect(get_str(url_name), **options)
