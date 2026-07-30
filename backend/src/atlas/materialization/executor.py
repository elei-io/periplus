"""Materialization process composition."""

from __future__ import annotations

import asyncio

from atlas.ingestion.objects.config import object_store_from_env
from atlas.ingestion.objects.html import RawHtmlRepository
from atlas.materialization.runtime import run_materialization
from atlas.platform.health import HealthMonitor
from atlas.platform.messaging.client import connect_nats


async def run(
    *,
    stop: asyncio.Event,
    monitor: HealthMonitor,
    concurrency: int,
) -> None:
    client = await connect_nats()
    try:
        repository = RawHtmlRepository(
            object_store_from_env(maximum_concurrency=concurrency)
        )
        monitor.dependencies_ready()
        monitor.subsystem_ready("materialization")
        await run_materialization(
            client.jetstream(),
            repository,
            stop=stop,
            concurrency=concurrency,
        )
    finally:
        await client.drain()
