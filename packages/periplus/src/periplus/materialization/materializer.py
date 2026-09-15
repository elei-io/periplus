"""One bounded live materialization lane consuming frozen visit evidence."""
import asyncio
import logging
import os
from threading import Timer

from nats.errors import TimeoutError as NatsTimeoutError

from periplus.ingestion.queue import (
    IngestionJob, MATERIALIZER_DURABLE, STREAM, VISIT_SUBJECT, ensure_repository_stream,
)
from periplus.ingestion.service import RepositoryIngestor, repository_ingestor_from_env
from periplus.materialization.storage import MaterialStore, build_material
from periplus.platform.config.performance import MATERIALIZATION_CATALOGUE_HARD_TIMEOUT_SECONDS
from periplus.platform.health import HealthMonitor
from periplus.platform.messaging.client import connect_nats
from periplus.platform.process import run_worker_process
from periplus.platform.telemetry import event

logger = logging.getLogger(__name__)


def _fail_stop() -> None:
    logger.critical("materialization exceeded its bounded execution deadline")
    os._exit(70)


def materialize_job(job: IngestionJob, ingestor: RepositoryIngestor, store: MaterialStore) -> bool:
    if job.kind != "visit" or job.visit is None:
        raise ValueError("materializer requires frozen visit evidence")
    timer = Timer(MATERIALIZATION_CATALOGUE_HARD_TIMEOUT_SECONDS, _fail_stop)
    timer.daemon = True
    timer.start()
    try:
        content, visit = build_material(job.visit, ingestor)
        return store.publish(content, visit)
    finally:
        timer.cancel()
        timer.join()


async def _apply(message, ingestor: RepositoryIngestor, store: MaterialStore) -> None:
    job = IngestionJob.model_validate_json(message.data)
    operation = asyncio.create_task(asyncio.to_thread(materialize_job, job, ingestor, store))
    try:
        while not operation.done():
            done, _ = await asyncio.wait({operation}, timeout=10)
            if not done:
                await message.in_progress()
        created = operation.result()
        await message.ack_sync()
        event("visit_materialized", operation_id=str(job.identity), created=created)
    finally:
        # Cancellation cannot release the client while its bounded writer runs.
        if not operation.done():
            await asyncio.shield(operation)


async def consume(subscription, ingestor: RepositoryIngestor, store: MaterialStore,
                  monitor: HealthMonitor, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            messages = await subscription.fetch(batch=1, timeout=1)
        except (NatsTimeoutError, TimeoutError):
            continue
        for message in messages:
            try:
                await _apply(message, ingestor, store)
                monitor.subsystem_ready("materialization")
            except asyncio.CancelledError:
                raise
            except Exception:
                # An ambiguous insert is reconciled by exact digest on retry.
                logger.exception("materialization delivery remains pending sequence=%s",
                                 message.metadata.sequence.stream)
                monitor.subsystem_unavailable("materialization", "delivery_failed")
                await message.nak(delay=30)


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor()
    client = await connect_nats()
    ingestor = None
    try:
        jetstream = client.jetstream()
        await ensure_repository_stream(jetstream)
        ingestor = await asyncio.to_thread(repository_ingestor_from_env)
        store = MaterialStore(ingestor.evidence_store.client)
        await asyncio.to_thread(ingestor.validate)
        await asyncio.to_thread(store.validate)
        subscription = await jetstream.pull_subscribe(VISIT_SUBJECT,
            durable=MATERIALIZER_DURABLE, stream=STREAM)
        monitor.dependencies_ready()
        monitor.subsystem_ready("materialization")
        await run_worker_process(role="materializer", monitor=monitor, stop=stop,
            tasks={"materialization": consume(subscription, ingestor, store, monitor, stop)})
    finally:
        if ingestor is not None:
            await asyncio.to_thread(ingestor.close)
        await client.close()
