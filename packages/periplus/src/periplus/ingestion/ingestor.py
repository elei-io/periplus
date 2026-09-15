"""Shared archive-import, live projection and historical rebuild workers."""

import asyncio
from periplus.ingestion.imports.worker import ImportLane
from periplus.materialization.rebuilds.runtime import (
    coordinator,
    consume,
    ensure_material_consumer,
)
from periplus.platform.health import HealthMonitor
from periplus.platform.messaging.catalogue_queue import ensure_catalogue_work_stream
from periplus.platform.messaging.client import connect_nats
from periplus.platform.messaging.leases import ensure_operation_lease_storage
from periplus.platform.process import run_worker_process


async def run() -> None:
    stop, monitor = asyncio.Event(), HealthMonitor()
    client = await connect_nats()
    imports = None
    try:
        jetstream = client.jetstream()
        await ensure_catalogue_work_stream(jetstream)
        await ensure_material_consumer(jetstream)
        leases = await ensure_operation_lease_storage(jetstream)
        imports = ImportLane(leases=leases, client=client, monitor=monitor)
        monitor.dependencies_ready()
        await run_worker_process(
            role="ingestor",
            monitor=monitor,
            stop=stop,
            tasks={
                "archive-planner": coordinator(jetstream, leases, monitor, stop),
                "processing": consume(jetstream, stop, 0, idle=imports.step),
            },
        )
    finally:
        if imports is not None:
            imports.close()
        await client.drain()
