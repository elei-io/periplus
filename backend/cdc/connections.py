"""Connections to the Basin-owned CDC namespace."""

from __future__ import annotations

from runtime.nats_client import connect_configured_nats


async def connect_basin_cdc(*, connect_timeout: int = 2):
    return await connect_configured_nats(
        url_name="DUCKBASIN_NATS_URL",
        seed_name="DUCKBASIN_NATS_SEED",
        connect_timeout=connect_timeout,
    )
