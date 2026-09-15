"""Materializer process composition; the rebuild runtime owns execution."""
import asyncio
import logging
import os
from periplus.ingestion.queue import ensure_repository_stream
from periplus.platform.health import HealthMonitor
from periplus.platform.messaging.client import connect_nats
from periplus.platform.process import run_worker_process


def _fail_stop() -> None:
    logging.getLogger(__name__).critical("Materialization exceeded its bounded execution deadline")
    os._exit(70)


async def run() -> None:
    from periplus.materialization.rebuilds.runtime import run_rebuilds
    from periplus.platform.messaging.leases import ensure_operation_lease_storage
    from periplus.ingestion.queue import ensure_dead_letter_stream
    stop = asyncio.Event()
    monitor = HealthMonitor()
    client = await connect_nats()
    try:
        jetstream = client.jetstream()
        await ensure_repository_stream(jetstream)
        await ensure_dead_letter_stream(jetstream)
        leases = await ensure_operation_lease_storage(jetstream)
        monitor.dependencies_ready()
        await run_worker_process(role="materializer", monitor=monitor, stop=stop,
            tasks={"materialization": run_rebuilds(jetstream, leases, monitor, stop)})
    finally:
        await client.close()
