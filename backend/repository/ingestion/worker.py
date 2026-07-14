"""DuckLake ingestion loop owned by the ingestion worker process."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from datetime import UTC, datetime

from nats.errors import TimeoutError as NatsTimeoutError
from repository.catalogue import (
    CatalogueConflictError,
    CatalogueValidationError,
    CrawlRecord,
    DocumentRecord,
)
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
    max_delivery_attempts,
    publish_dead_letter,
    store_ingestion_response,
)
from repository.catalogue.operations import (
    operation_locks as catalogue_operation_locks,
    run_with_catalogue_retry,
)
from repository.service import repository_ingestor_from_env
from runtime.catalogue_lane import catalogue_operation_lane
from runtime.graph_queue import (
    READINESS_SUBJECT,
    ReadinessWork,
    ensure_graph_progress_storage,
    ensure_graph_storage,
    settle_sample_request,
)
from runtime.graph_runs import settle_request
from runtime.navigation import (
    navigation_event_id,
    navigation_object_name,
    put_navigation_package,
)
from runtime.navigation_contract import NavigationPackage
from runtime.operation_leases import (
    OperationLeaseLost,
    OperationLeaseUnavailable,
    ensure_operation_lease_storage,
    operation_leases,
)
from runtime.resource_governor import (
    catalogue_request,
    ensure_resource_governor_storage,
    object_request,
    object_units,
    resource_permits,
)


async def _publish_navigation_readiness(
    client, job: IngestionJob, package: NavigationPackage
) -> None:
    crawl = job.crawl
    if crawl.graph_run_id is None or crawl.crawl_request_id is None:
        raise ValueError("navigation readiness requires graph runtime provenance")
    event = ReadinessWork(
        event_id=navigation_event_id(crawl.crawl_id, package.sha256),
        crawl_id=crawl.crawl_id,
        graph_run_id=crawl.graph_run_id,
        crawl_request_id=crawl.crawl_request_id,
        navigation=package,
        occurred_at=datetime.now(UTC),
    )
    await client.jetstream().publish(
        READINESS_SUBJECT,
        event.model_dump_json().encode(),
        headers={"Nats-Msg-Id": str(event.event_id)},
    )


async def _settle_graph_ingestion_failure(
    client, job: IngestionJob, error: str
) -> None:
    """Make a terminal repository failure terminal in graph execution too."""

    jetstream = client.jetstream()
    runs, requests, _workers = await ensure_graph_storage(jetstream)
    if job.crawl.purpose == "sample":
        await settle_sample_request(
            jetstream,
            requests,
            job.crawl.crawl_request_id,
            status="failed",
            error=f"Repository ingestion failed: {error}",
            failure_stage="enrichment",
        )
        return
    progress = await ensure_graph_progress_storage(jetstream)
    await settle_request(
        runs=runs,
        requests=requests,
        progress=progress,
        request_id=job.crawl.crawl_request_id,
        status="failed",
        error=f"Repository ingestion failed: {error}",
        failure_stage="enrichment",
    )


async def run(
    initialized: asyncio.Event | None = None, monitor: HealthMonitor | None = None
) -> None:
    stop = asyncio.Event()
    config = IngestionWorkerConfig.from_env()
    catalogue_request(
        "ingestion-startup-validation",
        service_class="critical",
        object_read_units=object_units(config.max_staged_bytes),
        object_write_units=object_units(config.max_staged_bytes),
    )
    client = await connect_repository_nats()
    jetstream = client.jetstream()
    await ensure_repository_stream(jetstream)
    await ensure_dead_letter_stream(jetstream)
    results_store = await ensure_ingestion_results(jetstream)
    operation_lease_store = await ensure_operation_lease_storage(jetstream)
    resource_grants = await ensure_resource_governor_storage(jetstream)
    await ensure_repository_consumer(jetstream)
    subscription = await jetstream.pull_subscribe(
        SUBJECT,
        durable=DURABLE,
        stream=STREAM,
    )
    ingestor = repository_ingestor_from_env()
    navigation_store = ingestor.html_repository.store
    await asyncio.to_thread(ingestor.validate)
    health_ingestor = repository_ingestor_from_env()
    await asyncio.to_thread(health_ingestor.validate)
    next_queue_snapshot = 0.0
    metrics_server = None
    if get_bool("ATLAS_METRICS_ENABLED"):
        metrics_server, _metrics_thread = start_http_server(
            get_int("ATLAS_INGESTION_WORKER_METRICS_PORT"),
            addr=get_str("ATLAS_METRICS_HOST"),
        )
    health_monitor = monitor or HealthMonitor(
        heartbeat_timeout_seconds=float(
            get_str("ATLAS_INGESTION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS")
        )
    )
    health_monitor.dependencies_ready()
    health_monitor.subsystem_ready("ingestion")
    health_server, _health_thread = start_health_server(
        address=get_str("ATLAS_INGESTION_WORKER_HEALTH_HOST"),
        port=get_int("ATLAS_INGESTION_WORKER_HEALTH_PORT"),
        monitor=health_monitor,
    )
    if initialized is not None:
        initialized.set()
    health_heartbeat_task = asyncio.create_task(_health_heartbeat(health_monitor))
    dependency_probe_task = asyncio.create_task(
        _dependency_probe(
            client,
            health_ingestor,
            health_monitor,
            catalogue_operation_lane(),
            resource_grants,
        )
    )
    heartbeat_task = None
    # Keep all access to this embedded connection explicit and serialized. Object
    # reads and DOM parsing occur outside the lock.
    catalogue_connection_lock = catalogue_operation_lane()
    jobs: list[IngestionJob] = []
    prepared = []
    accepted_messages = []
    batch_started_at: float | None = None

    async def flush_prepared() -> None:
        nonlocal heartbeat_task, batch_started_at
        if not prepared:
            return
        await _commit_batch_isolated(
            client,
            results_store,
            ingestor,
            list(jobs),
            list(accepted_messages),
            list(prepared),
            catalogue_connection_lock,
            navigation_store,
            operation_lease_store,
            resource_grants,
        )
        jobs.clear()
        accepted_messages.clear()
        prepared.clear()
        batch_started_at = None
        await client.flush()
        await _cancel_task(heartbeat_task)
        heartbeat_task = None

    try:
        while not stop.is_set():
            if (
                prepared
                and batch_started_at is not None
                and time.monotonic() - batch_started_at >= config.max_wait_seconds
            ):
                await flush_prepared()
                continue
            if time.monotonic() >= next_queue_snapshot:
                try:
                    info = await jetstream.consumer_info(STREAM, DURABLE)
                    pending = int(info.num_pending or 0) + int(
                        info.num_ack_pending or 0
                    )
                    queue_age = health_monitor.queue_observed(
                        "ingestion",
                        pending=pending,
                        progress_marker=(
                            getattr(info.delivered, "stream_seq", 0),
                            getattr(info.ack_floor, "stream_seq", 0),
                        ),
                        stalled_after_seconds=get_float(
                            "ATLAS_WORKER_QUEUE_STALL_SECONDS"
                        ),
                    )
                    repository_metrics.queue_state(
                        pending=info.num_pending,
                        ack_pending=info.num_ack_pending,
                        redelivered=info.num_redelivered,
                        oldest_pending_age_seconds=queue_age,
                    )
                except Exception:
                    logging.warning(
                        "repository ingestion queue metrics unavailable",
                        exc_info=True,
                    )
                next_queue_snapshot = time.monotonic() + 5
            available = max(1, config.max_items - len(prepared))
            fetch_timeout = config.max_wait_seconds
            if batch_started_at is not None:
                fetch_timeout = max(
                    0.001,
                    config.max_wait_seconds - (time.monotonic() - batch_started_at),
                )
            try:
                messages = await subscription.fetch(
                    batch=available,
                    timeout=fetch_timeout,
                )
            except (NatsTimeoutError, asyncio.TimeoutError):
                if prepared:
                    await flush_prepared()
                continue

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
                async with resource_permits(
                    resource_grants,
                    catalogue_request(
                        "ingestion-document-preload", service_class="critical"
                    ),
                ):
                    async with catalogue_connection_lock:
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

            for message, job in decoded_messages:
                preparation_started = time.perf_counter()
                durable_state = await ensure_pending_ingestion(
                    results_store,
                    request_id=job.request_id,
                    crawl=job.crawl,
                )
                if durable_state.status != "pending":
                    if durable_state.status == "succeeded":
                        try:
                            if job.crawl.purpose == "use" and durable_state.navigation is not None:
                                await _publish_navigation_readiness(
                                    client, job, durable_state.navigation
                                )
                            if job.crawl.purpose == "sample":
                                _runs, requests, _workers = await ensure_graph_storage(
                                    jetstream
                                )
                                await settle_sample_request(
                                    jetstream,
                                    requests,
                                    job.crawl.crawl_request_id,
                                    status="completed",
                                )
                        except Exception:
                            logging.exception(
                                "durable repository success notification failed"
                            )
                            await message.nak(delay=1)
                            continue
                        await message.ack()
                    else:
                        await _notify(client, job, durable_state)
                        await _dead_letter_or_retry(
                            client, message, job, durable_state.error or "ingestion failed"
                        )
                    continue
                try:
                    job_known_documents = known_documents
                    if job_known_documents is None:
                        job_known_documents = await _known_document_for_crawl(
                            ingestor,
                            job.crawl,
                            catalogue_connection_lock,
                            resource_grants,
                        )
                    async with resource_permits(
                        resource_grants,
                        object_request(
                            f"ingestion-raw-read:{job.request_id}",
                            direction="read",
                            byte_count=1,
                            service_class="critical",
                        ),
                    ):
                        value = await asyncio.to_thread(
                            ingestor.prepare_from_raw,
                            crawl=job.crawl,
                            known_documents=job_known_documents,
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
                        client,
                        results_store,
                        ingestor,
                        navigation_store,
                        message,
                        job,
                        exc,
                        catalogue_connection_lock,
                        resource_grants,
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
                if batch_started_at is None:
                    batch_started_at = time.monotonic()
                    heartbeat_task = asyncio.create_task(
                        _heartbeat_messages(accepted_messages)
                    )
                if _batch_reached_limit(prepared, config=config):
                    await flush_prepared()
    finally:
        stop.set()
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
    argparse.ArgumentParser(description="Run the Atlas ingestion loop.").parse_args()
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
    catalogue_connection_lock: asyncio.Lock,
    navigation_store,
    operation_lease_store,
    resource_grants,
) -> None:
    """Commit valid jobs while recursively isolating deterministic poison entries."""

    commit_started = time.perf_counter()
    element_rows = sum(value.element_count for value in prepared)
    staged_bytes = sum(value.staged_bytes for value in prepared)
    try:
        def commit_fenced():
            def attempt():
                with catalogue_operation_locks(job.request_id for job in jobs):
                    return ingestor.commit_prepared_batch(
                        prepared,
                        cleanup_on_error=False,
                    )

            return run_with_catalogue_retry(
                attempt, description="repository ingestion commit"
            )

        async with resource_permits(
            resource_grants,
            catalogue_request(
                f"ingestion-batch:{jobs[0].request_id}",
                service_class="critical",
                object_read_units=object_units(staged_bytes),
                object_write_units=object_units(staged_bytes),
            ),
        ):
            async with operation_leases(
                operation_lease_store,
                (job.request_id for job in jobs),
                phase="ingestion-commit",
            ):
                async with catalogue_connection_lock:
                    results = await asyncio.to_thread(commit_fenced)
    except (OperationLeaseUnavailable, OperationLeaseLost):
        repository_metrics.batch(
            outcome="contended",
            duration_seconds=time.perf_counter() - commit_started,
            items=len(prepared),
            element_rows=element_rows,
            staged_bytes=staged_bytes,
        )
        await asyncio.to_thread(ingestor.discard_prepared, prepared)
        for message in messages:
            await message.nak(delay=1)
        return
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
                catalogue_connection_lock,
                navigation_store,
                operation_lease_store,
                resource_grants,
            )
            await _commit_batch_isolated(
                client,
                results_store,
                ingestor,
                jobs[midpoint:],
                messages[midpoint:],
                prepared[midpoint:],
                catalogue_connection_lock,
                navigation_store,
                operation_lease_store,
                resource_grants,
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
                    client,
                    results_store,
                    ingestor,
                    navigation_store,
                    message,
                    job,
                    exc,
                    catalogue_connection_lock,
                    resource_grants,
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
            client,
            results_store,
            ingestor,
            navigation_store,
            messages[0],
            job,
            exc,
            catalogue_connection_lock,
            resource_grants,
        )
        return

    repository_metrics.batch(
        outcome="succeeded",
        duration_seconds=time.perf_counter() - commit_started,
        items=len(prepared),
        element_rows=element_rows,
        staged_bytes=staged_bytes,
    )
    for job, message, result, value in zip(
        jobs, messages, results, prepared, strict=True
    ):
        repository_metrics.attempt(
            outcome="succeeded",
            queue_seconds=(datetime.now(UTC) - job.enqueued_at).total_seconds(),
        )
        try:
            package = None
            if job.crawl.purpose == "use" and value.navigation_payload is not None:
                if job.crawl.graph_run_id is None or job.crawl.document_id is None:
                    raise ValueError("graph crawl ingestion requires runtime provenance")
                name = navigation_object_name(
                    job.crawl.graph_run_id,
                    job.crawl.document_id,
                    job.crawl.page_url,
                )
                async with resource_permits(
                    resource_grants,
                    object_request(
                        f"navigation-write:{job.request_id}",
                        direction="write",
                        byte_count=len(value.navigation_payload),
                        service_class="critical",
                    ),
                ):
                    package = await asyncio.to_thread(
                        put_navigation_package,
                        navigation_store,
                        name=name,
                        payload=value.navigation_payload,
                        row_count=value.navigation_row_count,
                    )
            durable_state = await store_ingestion_response(
                results_store,
                job=job,
                result=result,
                navigation=package,
            )
            if job.crawl.purpose == "use" and durable_state.navigation is not None:
                await _publish_navigation_readiness(
                    client, job, durable_state.navigation
                )
            if job.crawl.purpose == "sample":
                _runs, requests, _workers = await ensure_graph_storage(
                    client.jetstream()
                )
                await settle_sample_request(
                    client.jetstream(),
                    requests,
                    job.crawl.crawl_request_id,
                    status="completed",
                )
        except Exception:
            logging.exception(
                "repository ingestion committed but navigation publication failed"
            )
            await message.nak(delay=1)
            continue
        await message.ack()


async def _retry_or_fail(
    client,
    results_store,
    ingestor,
    navigation_store,
    message,
    job: IngestionJob,
    exc: Exception,
    catalogue_connection_lock: asyncio.Lock,
    resource_grants,
) -> None:
    deliveries = message.metadata.num_delivered
    if deliveries >= max_delivery_attempts():
        try:
            async with resource_permits(
                resource_grants,
                catalogue_request(
                    f"ingestion-reconcile:{job.request_id}",
                    service_class="critical",
                    object_read_units=1,
                ),
            ):
                async with catalogue_connection_lock:
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

        package = None
        if (
            reconciled is not None
            and job.crawl.purpose == "use"
            and job.crawl.graph_run_id is not None
        ):
            try:
                known_documents = await _known_document_for_crawl(
                    ingestor,
                    job.crawl,
                    catalogue_connection_lock,
                    resource_grants,
                )
                async with resource_permits(
                    resource_grants,
                    object_request(
                        f"ingestion-recovery-read:{job.request_id}",
                        direction="read",
                        byte_count=1,
                        service_class="critical",
                    ),
                ):
                    prepared = await asyncio.to_thread(
                        ingestor.prepare_from_raw,
                        crawl=job.crawl,
                        known_documents=known_documents,
                    )
                try:
                    if prepared.navigation_payload is not None:
                        if job.crawl.document_id is None:
                            raise ValueError(
                                "navigation package requires a document identity"
                            )
                        async with resource_permits(
                            resource_grants,
                            object_request(
                                f"navigation-recovery-write:{job.request_id}",
                                direction="write",
                                byte_count=len(prepared.navigation_payload),
                                service_class="critical",
                            ),
                        ):
                            package = await asyncio.to_thread(
                                put_navigation_package,
                                navigation_store,
                                name=navigation_object_name(
                                    job.crawl.graph_run_id,
                                    job.crawl.document_id,
                                    job.crawl.page_url,
                                ),
                                payload=prepared.navigation_payload,
                                row_count=prepared.navigation_row_count,
                            )
                finally:
                    await asyncio.to_thread(ingestor.discard_prepared, [prepared])
            except Exception as navigation_exc:
                logging.exception("navigation package recovery failed")
                exc = navigation_exc
                reconciled = None
        durable_state = await store_ingestion_response(
            results_store,
            job=job,
            result=reconciled,
            navigation=package,
            error=None if reconciled is not None else _exception_message(exc),
        )
        if job.crawl.purpose == "use" and durable_state.navigation is not None:
            try:
                await _publish_navigation_readiness(
                    client, job, durable_state.navigation
                )
            except Exception:
                logging.exception("recovered navigation readiness publication failed")
                await message.nak(delay=1)
                return
        if durable_state.status == "succeeded":
            if job.crawl.purpose == "sample":
                _runs, requests, _workers = await ensure_graph_storage(
                    client.jetstream()
                )
                await settle_sample_request(
                    client.jetstream(),
                    requests,
                    job.crawl.crawl_request_id,
                    status="completed",
                )
            await message.ack()
        else:
            await _dead_letter_or_retry(
                client, message, job, durable_state.error or _exception_message(exc)
            )
        return
    await message.nak(delay=min(30, 2 ** max(0, deliveries - 1)))


def _exception_message(exc: BaseException) -> str:
    """Retain concise root-cause context in durable operational state."""

    messages: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        message = str(current).strip() or type(current).__name__
        if not messages or message != messages[-1]:
            messages.append(message)
        current = current.__cause__
    return ": ".join(messages)


async def _dead_letter_or_retry(client, message, job: IngestionJob, error: str) -> None:
    """Only remove terminal work after its durable failure copy is acknowledged."""

    try:
        await _settle_graph_ingestion_failure(client, job, error)
    except Exception:
        logging.warning(
            "repository failure could not settle graph request; retaining work",
            exc_info=True,
        )
        await message.nak(delay=30)
        return
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


async def _known_document_for_crawl(
    ingestor,
    crawl: CrawlRecord,
    catalogue_connection_lock: asyncio.Lock,
    resource_grants,
) -> dict[str, DocumentRecord]:
    """Read canonical document state without holding the connection during parsing."""

    if crawl.document_id is None:
        return {}
    async with resource_permits(
        resource_grants,
        catalogue_request(
            f"ingestion-document-read:{crawl.crawl_id}", service_class="critical"
        ),
    ):
        async with catalogue_connection_lock:
            existing = await asyncio.to_thread(
                ingestor.catalogue_service.get_document,
                crawl.document_id,
            )
    return {crawl.document_id: existing} if existing is not None else {}


async def _health_heartbeat(monitor: HealthMonitor) -> None:
    while True:
        monitor.heartbeat()
        await asyncio.sleep(1)


async def _dependency_probe(
    client,
    ingestor,
    monitor: HealthMonitor,
    catalogue_connection_lock: asyncio.Lock,
    resource_grants,
) -> None:
    interval = get_float("ATLAS_INGESTION_WORKER_HEALTH_PROBE_INTERVAL_SECONDS")
    timeout = get_float("ATLAS_INGESTION_WORKER_HEALTH_PROBE_TIMEOUT_SECONDS")
    if interval <= 0 or timeout <= 0:
        monitor.dependencies_unavailable(
            "repository health probe intervals must be greater than zero"
        )
        return
    while True:
        try:
            await asyncio.wait_for(client.flush(), timeout=timeout)
            async with asyncio.timeout(timeout):
                async with resource_permits(
                    resource_grants,
                    catalogue_request(
                        "ingestion-health-probe", service_class="critical"
                    ),
                ):
                    async with catalogue_connection_lock:
                        await asyncio.to_thread(ingestor.validate)
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
