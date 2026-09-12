"""Materialization process composition."""

from __future__ import annotations

import asyncio

from periplus.ingestion.objects.config import object_store_from_env
from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.materialization.runtime import run_materialization
from periplus.platform.config.environment import get_int
from periplus.platform.health import HealthMonitor
from periplus.platform.messaging.client import connect_nats


async def run(
    *,
    stop: asyncio.Event,
    monitor: HealthMonitor,
    concurrency: int,
) -> None:
    parser_processes = get_int("PERIPLUS_MATERIALIZER_PARSER_PROCESSES", minimum=1)
    if parser_processes > 8:
        raise ValueError("parser processes must be between 1 and 8")
    read_concurrency = concurrency * min(8, parser_processes * 2)
    client = await connect_nats()
    try:
        repository = RawHtmlRepository(
            object_store_from_env(maximum_concurrency=read_concurrency)
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
