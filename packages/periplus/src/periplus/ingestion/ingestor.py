"""Archive-import process; native crawlers publish directly to the archive."""

import asyncio
from periplus.ingestion.imports.worker import run as run_imports
from periplus.platform.health import HealthMonitor
from periplus.platform.messaging.catalogue_queue import ensure_catalogue_work_stream
from periplus.platform.messaging.client import connect_nats
from periplus.platform.messaging.leases import ensure_operation_lease_storage
from periplus.platform.process import run_worker_process


async def run() -> None:
    stop, monitor = asyncio.Event(), HealthMonitor()
    client = await connect_nats()
    try:
        await ensure_catalogue_work_stream(client.jetstream())
        leases = await ensure_operation_lease_storage(client.jetstream())
        monitor.dependencies_ready()
        await run_worker_process(
            role="ingestor",
            monitor=monitor,
            stop=stop,
            tasks={
                "archive-imports": run_imports(
                    stop=stop, monitor=monitor, leases=leases, client=client
                )
            },
        )
    finally:
        await client.drain()
