"""Symmetric materializer replicas: a shared planner lease and parallel workers."""

import asyncio
from periplus.materialization.rebuilds.runtime import (
    run_rebuilds,
    ensure_material_consumer,
)
from periplus.platform.messaging.catalogue_queue import ensure_catalogue_work_stream
from periplus.platform.health import HealthMonitor
from periplus.platform.messaging.client import connect_nats
from periplus.platform.messaging.leases import ensure_operation_lease_storage
from periplus.platform.process import run_worker_process


async def run() -> None:
    stop, monitor = asyncio.Event(), HealthMonitor()
    client = await connect_nats()
    try:
        jetstream = client.jetstream()
        await ensure_catalogue_work_stream(jetstream)
        await ensure_material_consumer(jetstream)
        leases = await ensure_operation_lease_storage(jetstream)
        monitor.dependencies_ready()
        await run_worker_process(
            role="materializer",
            monitor=monitor,
            stop=stop,
            tasks={"materialization": run_rebuilds(jetstream, leases, monitor, stop)},
        )
    finally:
        await client.close()
