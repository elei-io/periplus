"""Dedicated durable repository-ingestion worker."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import time
from datetime import UTC, datetime

from nats.errors import TimeoutError as NatsTimeoutError

from repository.catalogue import CatalogueConflictError, CatalogueValidationError
from observability import repository_metrics
from prometheus_client import start_http_server
from config import get_bool, get_float, get_int, get_str
from repository.ingestion.health import HealthMonitor, start_health_server
from repository.ingestion.pipeline import IngestionWorkerConfig
from repository.ingestion.queue import (
    DURABLE,
    STREAM,
    SUBJECT,
    IngestionJob,
    ack_wait_seconds,
    connect_repository_nats,
    ensure_dead_letter_stream,
    ensure_ingestion_results,
    ensure_repository_consumer,
    ensure_pending_ingestion,
    ensure_repository_stream,
    ingestion_response,
    max_delivery_attempts,
    publish_dead_letter,
    store_ingestion_response,
)
from repository.service import repository_ingestor_from_env


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for value in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(value, stop.set)

    config = IngestionWorkerConfig.from_env()
    client = await connect_repository_nats()
    jetstream = client.jetstream()
    await ensure_repository_stream(jetstream)
    await ensure_dead_letter_stream(jetstream)
    results_store = await ensure_ingestion_results(jetstream)
    await ensure_repository_consumer(jetstream)
    subscription = await jetstream.pull_subscribe(
        SUBJECT,
        durable=DURABLE,
        stream=STREAM,
    )
    ingestor = repository_ingestor_from_env()
    await asyncio.to_thread(ingestor.validate)
    health_ingestor = repository_ingestor_from_env()
    await asyncio.to_thread(health_ingestor.validate)
    staging_cleanup_interval = float(
        get_str("ATLAS_INGEST_STAGING_CLEANUP_INTERVAL_SECONDS")
    )
    staging_grace = get_float("ATLAS_INGEST_STAGING_GRACE_SECONDS")
    if staging_cleanup_interval <= 0 or staging_grace <= 0:
        raise ValueError("repository staging cleanup intervals must be greater than zero")
    await asyncio.to_thread(
        ingestor.cleanup_staging,
        older_than_seconds=staging_grace,
    )
    next_staging_cleanup = time.monotonic() + staging_cleanup_interval
    next_queue_snapshot = 0.0
    metrics_server = None
    if get_bool("ATLAS_METRICS_ENABLED"):
        metrics_server, _metrics_thread = start_http_server(
            get_int("ATLAS_REPOSITORY_WORKER_METRICS_PORT"),
            addr=get_str("ATLAS_METRICS_HOST"),
        )
    health_monitor = HealthMonitor(
        heartbeat_timeout_seconds=float(
            get_str("ATLAS_REPOSITORY_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS")
        )
    )
    health_monitor.dependencies_ready()
    health_server, _health_thread = start_health_server(
        address=get_str("ATLAS_REPOSITORY_WORKER_HEALTH_HOST"),
        port=get_int("ATLAS_REPOSITORY_WORKER_HEALTH_PORT"),
        monitor=health_monitor,
    )
    health_heartbeat_task = asyncio.create_task(_health_heartbeat(health_monitor))
    dependency_probe_task = asyncio.create_task(
        _dependency_probe(client, health_ingestor, health_monitor)
    )
    heartbeat_task = None
    try:
        while not stop.is_set():
            if time.monotonic() >= next_queue_snapshot:
                try:
                    info = await jetstream.consumer_info(STREAM, DURABLE)
                    repository_metrics.queue_state(
                        pending=info.num_pending,
                        ack_pending=info.num_ack_pending,
                        redelivered=info.num_redelivered,
                    )
                except Exception:
                    logging.warning(
                        "repository ingestion queue metrics unavailable",
                        exc_info=True,
                    )
                next_queue_snapshot = time.monotonic() + 5
            if time.monotonic() >= next_staging_cleanup:
                deleted = await asyncio.to_thread(
                    ingestor.cleanup_staging,
                    older_than_seconds=staging_grace,
                )
                if deleted:
                    logging.info("removed %d abandoned repository staging files", deleted)
                next_staging_cleanup = time.monotonic() + staging_cleanup_interval
            try:
                messages = await subscription.fetch(
                    batch=config.max_items,
                    timeout=config.max_wait_seconds,
                )
            except (NatsTimeoutError, asyncio.TimeoutError):
                continue

            heartbeat_task = asyncio.create_task(_heartbeat_messages(messages))
            jobs: list[IngestionJob] = []
            prepared = []
            accepted_messages = []
            decoded_messages = []

            for message in messages:
                try:
                    decoded_messages.append(
                        (message, IngestionJob.model_validate_json(message.data))
                    )
                except Exception:
                    logging.exception("discarding invalid repository ingestion job")
                    await message.term()

            document_ids = [
                job.crawl.document_id
                for _, job in decoded_messages
                if job.crawl.document_id is not None
            ]
            try:
                known_documents = await asyncio.to_thread(
                    ingestor.catalogue_service.get_documents,
                    document_ids,
                )
            except Exception:
                logging.warning(
                    "repository batch document preload unavailable; falling back",
                    exc_info=True,
                )
                known_documents = None

            async def flush_prepared() -> None:
                if not prepared:
                    return
                await _commit_batch_isolated(
                    client,
                    results_store,
                    ingestor,
                    list(jobs),
                    list(accepted_messages),
                    list(prepared),
                )
                jobs.clear()
                accepted_messages.clear()
                prepared.clear()

            for message, job in decoded_messages:
                preparation_started = time.perf_counter()
                durable_state = await ensure_pending_ingestion(
                    results_store,
                    request_id=job.request_id,
                    crawl=job.crawl,
                )
                if durable_state.status != "pending":
                    await _notify(client, job, durable_state)
                    if durable_state.status == "succeeded":
                        await message.ack()
                    else:
                        await _dead_letter_or_retry(
                            client, message, job, durable_state.error or "ingestion failed"
                        )
                    continue
                try:
                    value = await asyncio.to_thread(
                        ingestor.prepare_from_raw,
                        crawl=job.crawl,
                        known_documents=known_documents,
                    )
                except Exception as exc:
                    repository_metrics.preparation(
                        outcome="failed",
                        duration_seconds=time.perf_counter() - preparation_started,
                    )
                    logging.exception("repository ingestion preparation failed")
                    repository_metrics.attempt(
                        outcome="failed",
                        queue_seconds=(datetime.now(UTC) - job.enqueued_at).total_seconds(),
                    )
                    await _retry_or_fail(
                        client, results_store, ingestor, message, job, exc
                    )
                    continue
                repository_metrics.preparation(
                    outcome="succeeded",
                    duration_seconds=time.perf_counter() - preparation_started,
                )
                if _would_exceed_batch(prepared, value, config=config):
                    await flush_prepared()
                jobs.append(job)
                prepared.append(value)
                accepted_messages.append(message)
                if _batch_reached_limit(prepared, config=config):
                    await flush_prepared()

            await flush_prepared()
            await client.flush()
            await _cancel_task(heartbeat_task)
            heartbeat_task = None
    finally:
        await _cancel_task(heartbeat_task)
        await _cancel_task(health_heartbeat_task)
        await _cancel_task(dependency_probe_task)
        await asyncio.to_thread(health_server.shutdown)
        health_server.server_close()
        await asyncio.to_thread(health_ingestor.close)
        await asyncio.to_thread(ingestor.close)
        await client.drain()
        if metrics_server is not None:
            await asyncio.to_thread(metrics_server.shutdown)
            metrics_server.server_close()


def main() -> None:
    argparse.ArgumentParser(description="Run the Atlas repository worker.").parse_args()
    logging.basicConfig(
        level=get_str("ATLAS_LOG_LEVEL"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


def _would_exceed_batch(prepared, value, *, config: IngestionWorkerConfig) -> bool:
    if not prepared:
        return False
    return (
        len(prepared) >= config.max_items
        or sum(item.element_count for item in prepared) + value.element_count
        > config.max_element_rows
        or sum(item.staged_bytes for item in prepared) + value.staged_bytes
        > config.max_staged_bytes
    )


def _batch_reached_limit(prepared, *, config: IngestionWorkerConfig) -> bool:
    """Flush full batches immediately; one page remains bounded by document limits."""

    return bool(prepared) and (
        len(prepared) >= config.max_items
        or sum(item.element_count for item in prepared) >= config.max_element_rows
        or sum(item.staged_bytes for item in prepared) >= config.max_staged_bytes
    )


async def _commit_batch_isolated(
    client,
    results_store,
    ingestor,
    jobs,
    messages,
    prepared,
) -> None:
    """Commit valid jobs while recursively isolating deterministic poison entries."""

    commit_started = time.perf_counter()
    element_rows = sum(value.element_count for value in prepared)
    staged_bytes = sum(value.staged_bytes for value in prepared)
    try:
        results = await asyncio.to_thread(
            ingestor.commit_prepared_batch,
            prepared,
            cleanup_on_error=False,
        )
    except Exception as exc:
        repository_metrics.batch(
            outcome="failed",
            duration_seconds=time.perf_counter() - commit_started,
            items=len(prepared),
            element_rows=element_rows,
            staged_bytes=staged_bytes,
        )
        if len(prepared) > 1 and isinstance(
            exc, (CatalogueConflictError, CatalogueValidationError)
        ):
            logging.warning(
                "repository ingestion microbatch failed; isolating entries",
                exc_info=True,
            )
            midpoint = len(prepared) // 2
            await _commit_batch_isolated(
                client,
                results_store,
                ingestor,
                jobs[:midpoint],
                messages[:midpoint],
                prepared[:midpoint],
            )
            await _commit_batch_isolated(
                client,
                results_store,
                ingestor,
                jobs[midpoint:],
                messages[midpoint:],
                prepared[midpoint:],
            )
            return

        if len(prepared) > 1:
            logging.exception(
                "repository ingestion microbatch unavailable; retrying batch"
            )
            await asyncio.to_thread(ingestor.discard_prepared, prepared)
            for job, message in zip(jobs, messages, strict=True):
                repository_metrics.attempt(
                    outcome="failed",
                    queue_seconds=(datetime.now(UTC) - job.enqueued_at).total_seconds(),
                )
                await _retry_or_fail(
                    client, results_store, ingestor, message, job, exc
                )
            return

        logging.exception("repository ingestion entry failed")
        await asyncio.to_thread(ingestor.discard_prepared, prepared)
        job = jobs[0]
        repository_metrics.attempt(
            outcome="failed",
            queue_seconds=(datetime.now(UTC) - job.enqueued_at).total_seconds(),
        )
        await _retry_or_fail(
            client, results_store, ingestor, messages[0], job, exc
        )
        return

    repository_metrics.batch(
        outcome="succeeded",
        duration_seconds=time.perf_counter() - commit_started,
        items=len(prepared),
        element_rows=element_rows,
        staged_bytes=staged_bytes,
    )
    for job, message, result in zip(jobs, messages, results, strict=True):
        repository_metrics.attempt(
            outcome="succeeded",
            queue_seconds=(datetime.now(UTC) - job.enqueued_at).total_seconds(),
        )
        durable_state = await store_ingestion_response(
            results_store,
            job=job,
            result=result,
        )
        await _notify(client, job, durable_state)
        await message.ack()


async def _retry_or_fail(
    client,
    results_store,
    ingestor,
    message,
    job: IngestionJob,
    exc: Exception,
) -> None:
    deliveries = message.metadata.num_delivered
    if deliveries >= max_delivery_attempts():
        try:
            reconciled = await asyncio.to_thread(
                ingestor.reconcile_crawl_commit,
                crawl=job.crawl,
            )
        except CatalogueConflictError as reconcile_exc:
            # The stable operation identity points at different durable data, so
            # it cannot be reconciled as this job's success.
            exc = reconcile_exc
            reconciled = None
        except Exception:
            # DuckLake is the commit authority. If it cannot be consulted, keep
            # the work live rather than publishing a potentially false failure.
            logging.warning(
                "repository ingestion failure reconciliation unavailable",
                exc_info=True,
            )
            await message.nak(delay=30)
            return

        durable_state = await store_ingestion_response(
            results_store,
            job=job,
            result=reconciled,
            error=None if reconciled is not None else str(exc),
        )
        await _notify(client, job, durable_state)
        if durable_state.status == "succeeded":
            await message.ack()
        else:
            await _dead_letter_or_retry(
                client, message, job, durable_state.error or str(exc)
            )
        return
    await message.nak(delay=min(30, 2 ** max(0, deliveries - 1)))


async def _notify(client, job: IngestionJob, durable_state) -> None:
    """Publish a best-effort wakeup after durable terminal state exists."""

    try:
        await client.publish(
            job.reply_subject,
            ingestion_response(durable_state).model_dump_json().encode(),
        )
    except Exception:
        logging.warning(
            "repository ingestion notification unavailable",
            exc_info=True,
        )


async def _dead_letter_or_retry(client, message, job: IngestionJob, error: str) -> None:
    """Only remove terminal work after its durable failure copy is acknowledged."""

    try:
        await publish_dead_letter(
            client.jetstream(),
            job=job,
            error=error,
            delivery_count=message.metadata.num_delivered,
        )
    except Exception:
        logging.warning(
            "repository dead-letter publication unavailable; retaining work",
            exc_info=True,
        )
        await message.nak(delay=30)
        return
    await message.term()


async def _heartbeat_messages(messages) -> None:
    interval = max(1.0, min(30.0, ack_wait_seconds() / 3))
    while True:
        await asyncio.sleep(interval)
        await asyncio.gather(
            *(message.in_progress() for message in messages),
            return_exceptions=True,
        )


async def _health_heartbeat(monitor: HealthMonitor) -> None:
    while True:
        monitor.heartbeat()
        await asyncio.sleep(1)


async def _dependency_probe(client, ingestor, monitor: HealthMonitor) -> None:
    interval = get_float("ATLAS_REPOSITORY_WORKER_HEALTH_PROBE_INTERVAL_SECONDS")
    timeout = get_float("ATLAS_REPOSITORY_WORKER_HEALTH_PROBE_TIMEOUT_SECONDS")
    if interval <= 0 or timeout <= 0:
        monitor.dependencies_unavailable(
            "repository health probe intervals must be greater than zero"
        )
        return
    while True:
        try:
            await asyncio.wait_for(client.flush(), timeout=timeout)
            await asyncio.wait_for(
                asyncio.to_thread(ingestor.validate),
                timeout=timeout,
            )
        except Exception as exc:
            monitor.dependencies_unavailable(str(exc) or type(exc).__name__)
        else:
            monitor.dependencies_ready()
        await asyncio.sleep(interval)


async def _cancel_task(task) -> None:
    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    main()
