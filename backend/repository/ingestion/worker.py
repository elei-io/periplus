"""DuckLake ingestion loop owned by the ingestion worker process."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
import logging
import os
import time
from datetime import UTC, datetime
from typing import Never

from nats.errors import TimeoutError as NatsTimeoutError
from repository.catalogue import (
    CatalogueConflictError,
    CatalogueValidationError,
    CrawlRecord,
    DocumentRecord,
    DuckBasinUnavailableError,
    ExistingCatalogueIdentities,
    ServiceAccountTokenProvider,
)
from observability import repository_metrics
from config import get_float
from config.performance import INGESTION_CATALOGUE_HARD_TIMEOUT_SECONDS
from repository.ingestion.health import HealthMonitor
from repository.ingestion.pipeline import IngestionWorkerConfig
from repository.ingestion.recovery import (
    DependencyAdmission,
    IngestionDependencyCircuit,
)
from repository.ingestion.queue import (
    DURABLE,
    STREAM,
    SUBJECT,
    IngestionJob,
    ack_wait_seconds,
    ensure_dead_letter_stream,
    ensure_ingestion_results,
    ensure_repository_consumer,
    ensure_pending_ingestion,
    ensure_repository_stream,
    max_delivery_attempts,
    publish_dead_letter,
    record_ingestion_processing_failure,
    store_ingestion_response,
)
from runtime.nats_client import connect_nats
from repository.catalogue.operations import (
    is_retryable_catalogue_unavailability,
    run_with_catalogue_retry,
)
from repository.service import PreparedIngestion, repository_ingestor_from_env
from runtime.catalogue_workers import CatalogueLaneReporter
from runtime.operation_leases import (
    OperationLeaseLost,
    OperationLeaseUnavailable,
    ensure_operation_lease_storage,
    operation_leases,
)
from workers.lifecycle import cancel_task


@dataclass(slots=True)
class _CommitRecoveryState:
    entered_commit: bool = False


async def run(
    *,
    stop: asyncio.Event,
    monitor: HealthMonitor,
    lane: CatalogueLaneReporter | None = None,
    lane_index: int = 0,
    circuit: IngestionDependencyCircuit | None = None,
    tokens: ServiceAccountTokenProvider | None = None,
) -> None:
    lane = lane or CatalogueLaneReporter(lane_index=lane_index)
    circuit = circuit or IngestionDependencyCircuit(1)
    lane.attach(
        lambda: monitor.status(
            include_liveness=False,
            include_queues=False,
        )
    )
    lane_metrics = repository_metrics.IngestionLaneMetrics(lane_index)
    config = IngestionWorkerConfig.defaults()
    client = await connect_nats()
    jetstream = client.jetstream()
    await ensure_repository_stream(jetstream)
    await ensure_dead_letter_stream(jetstream)
    results_store = await ensure_ingestion_results(jetstream)
    operation_lease_store = await ensure_operation_lease_storage(jetstream)
    await ensure_repository_consumer(jetstream)
    subscription = await jetstream.pull_subscribe(
        SUBJECT,
        durable=DURABLE,
        stream=STREAM,
    )
    admission = await circuit.admit(lane_index, stop=stop)
    if admission is None:
        await client.drain()
        return
    ingestor = None
    while ingestor is None and not stop.is_set():
        try:
            ingestor = await _catalogue_call(
                lambda: repository_ingestor_from_env(tokens=tokens),
                description="catalogue client creation",
            )
            await _catalogue_call(
                ingestor.validate,
                description="catalogue schema validation",
            )
        except DuckBasinUnavailableError as exc:
            delay = await _mark_catalogue_unavailable(
                circuit,
                monitor,
                exc,
                admission=admission,
            )
            lane_metrics.circuit("open")
            logging.warning(
                "repository ingestion catalogue unavailable during startup; "
                "retrying in %.3fs",
                delay,
                exc_info=True,
            )
            if ingestor is not None:
                await _catalogue_call(
                    ingestor.close,
                    description="catalogue client close after failed startup",
                )
                ingestor = None
            admission = await circuit.admit(lane_index, stop=stop)
            if admission is None:
                await client.drain()
                return
    if ingestor is None:
        await client.drain()
        return
    if admission.probe_required:
        await circuit.recovered(admission, lane_index)
    lane_metrics.circuit(await circuit.state())
    _refresh_lane_client_metrics(lane_metrics, ingestor)
    catalogue_connection_lock = asyncio.Lock()
    next_queue_snapshot = 0.0
    health_monitor = monitor
    health_monitor.dependencies_ready()
    health_monitor.subsystem_ready("ingestion")
    dependency_probe_task = (
        asyncio.create_task(
            _dependency_probe(
                client,
                ingestor,
                catalogue_connection_lock,
                health_monitor,
                lane_metrics,
                circuit=circuit,
            )
        )
        if lane_index == 0
        else None
    )
    heartbeat_task = None
    fetched_heartbeat_task = None
    active_operation_count = 0
    # Keep all access to this session-affine Basin connection explicit and
    # serialized. Object reads and DOM parsing occur outside the lock.
    jobs: list[IngestionJob] = []
    prepared = []
    accepted_messages = []
    fetched_messages: dict[int, object] = {}
    commit_recovery = _CommitRecoveryState()
    batch_started_at: float | None = None

    async def flush_prepared(reason: str) -> None:
        nonlocal heartbeat_task, batch_started_at, active_operation_count
        if not prepared:
            return
        repository_metrics.batch_flush(reason=reason)
        commit_recovery.entered_commit = False
        lane_metrics.operation_started("commit")
        try:
            await _commit_batch_isolated(
                client,
                results_store,
                ingestor,
                list(jobs),
                list(accepted_messages),
                list(prepared),
                catalogue_connection_lock,
                operation_lease_store,
                lane_metrics=lane_metrics,
                recovery_state=commit_recovery,
                circuit=circuit,
                monitor=health_monitor,
                admission=admission,
            )
            commit_recovery.entered_commit = False
        finally:
            _refresh_lane_client_metrics(lane_metrics, ingestor)
            lane_metrics.operation_finished()
        jobs.clear()
        accepted_messages.clear()
        prepared.clear()
        active_operation_count = 0
        lane.active_operation_count = 0
        batch_started_at = None
        await client.flush()
        await cancel_task(heartbeat_task)
        heartbeat_task = None

    async def release_uncommitted_prepared() -> None:
        nonlocal heartbeat_task, batch_started_at, active_operation_count
        if not prepared:
            return
        await asyncio.to_thread(ingestor.discard_prepared, list(prepared))
        delay = await circuit.retry_delay()
        for message in accepted_messages:
            await message.nak(delay=delay)
        jobs.clear()
        accepted_messages.clear()
        prepared.clear()
        lane_metrics.recovery("dependency_epoch_changed")
        active_operation_count = 0
        lane.active_operation_count = 0
        batch_started_at = None
        await cancel_task(heartbeat_task)
        heartbeat_task = None

    try:
        while not stop.is_set():
            lane_metrics.circuit(await circuit.state())
            if not await circuit.is_current(admission):
                health_monitor.dependencies_unavailable(
                    "catalogue dependency circuit is open"
                )
                await release_uncommitted_prepared()
            admission = await circuit.admit(lane_index, stop=stop)
            if admission is None:
                break
            if admission.probe_required:
                lane_metrics.circuit("half_open")
                try:
                    async with catalogue_connection_lock:
                        await _catalogue_call(
                            ingestor.probe,
                            description=(
                                f"catalogue recovery probe for lane {lane_index}"
                            ),
                        )
                except DuckBasinUnavailableError as exc:
                    await _mark_catalogue_unavailable(
                        circuit,
                        health_monitor,
                        exc,
                        admission=admission,
                    )
                    continue
                await circuit.recovered(admission, lane_index)
                health_monitor.dependencies_ready()
                lane_metrics.circuit(await circuit.state())
            if (
                prepared
                and batch_started_at is not None
                and time.monotonic() - batch_started_at >= config.max_wait_seconds
            ):
                await flush_prepared("max_wait")
                continue
            if time.monotonic() >= next_queue_snapshot:
                try:
                    info = await jetstream.consumer_info(STREAM, DURABLE)
                    pending = int(info.num_pending or 0) + int(
                        info.num_ack_pending or 0
                    )
                    stall_threshold = get_float(
                        "ATLAS_WORKER_QUEUE_STALL_SECONDS"
                    )
                    queue_age = health_monitor.queue_observed(
                        "ingestion",
                        pending=pending,
                        progress_marker=(
                            getattr(info.delivered, "stream_seq", 0),
                            getattr(info.ack_floor, "stream_seq", 0),
                        ),
                        stalled_after_seconds=stall_threshold,
                    )
                    repository_metrics.queue_state(
                        pending=info.num_pending,
                        ack_pending=info.num_ack_pending,
                        redelivered=info.num_redelivered,
                        oldest_pending_age_seconds=queue_age,
                        stalled=pending > 0
                        and queue_age > stall_threshold,
                    )
                except Exception:
                    logging.warning(
                        "repository ingestion queue metrics unavailable",
                        exc_info=True,
                    )
                next_queue_snapshot = time.monotonic() + 5
            available = (
                max(1, config.max_items - len(prepared)) if prepared else 1
            )
            fetch_timeout = 1.0
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
                    await flush_prepared("max_wait")
                continue
            if not await circuit.is_current(admission):
                for message in messages:
                    await message.nak(delay=1)
                continue

            decoded_messages = []

            for message in messages:
                try:
                    job = IngestionJob.model_validate_json(message.data)
                except Exception:
                    logging.exception("discarding invalid repository ingestion job")
                    await message.term()
                    continue
                decoded_messages.append((message, job))
                fetched_messages[id(message)] = message

            if decoded_messages:
                active_operation_count = 1
                lane.active_operation_count = 1

            document_ids = [
                job.crawl.document_id
                for _, job in decoded_messages
                if job.crawl.document_id is not None
            ]
            fetched_heartbeat_task = asyncio.create_task(
                _heartbeat_messages([message for message, _job in decoded_messages])
            )
            lane_metrics.operation_started("preload")
            try:
                async with catalogue_connection_lock:
                    known_documents = await _catalogue_call(
                        ingestor.catalogue_service.get_documents,
                        document_ids,
                        description="repository batch document preload",
                    )
            except DuckBasinUnavailableError as exc:
                delay = await _mark_catalogue_unavailable(
                    circuit,
                    health_monitor,
                    exc,
                    admission=admission,
                )
                logging.warning(
                    "repository batch document preload unavailable; "
                    "retaining deliveries",
                    exc_info=True,
                )
                for message, _job in decoded_messages:
                    await message.nak(delay=delay)
                    fetched_messages.pop(id(message), None)
                await cancel_task(fetched_heartbeat_task)
                fetched_heartbeat_task = None
                continue
            except Exception:
                logging.warning(
                    "repository batch document preload unavailable; falling back",
                    exc_info=True,
                )
                known_documents = None
            finally:
                _refresh_lane_client_metrics(lane_metrics, ingestor)
                lane_metrics.operation_finished()

            for message, job in decoded_messages:
                preparation_started = time.perf_counter()
                durable_state = await ensure_pending_ingestion(
                    results_store,
                    request_id=job.request_id,
                    crawl=job.crawl,
                    crawl_attempts=job.crawl_attempts,
                    crawl_steps=job.crawl_steps,
                )
                if durable_state.status != "pending":
                    if durable_state.status == "succeeded":
                        await message.ack()
                    else:
                        await _notify(client, job, durable_state)
                        await _dead_letter_or_retry(
                            client,
                            message,
                            job,
                            durable_state.error or "ingestion failed",
                            durable_state.processing_failure_count,
                        )
                    fetched_messages.pop(id(message), None)
                    continue
                lane_metrics.operation_started("prepare")
                try:
                    value = await _prepare_ingestion_job(
                        ingestor,
                        message,
                        job,
                        known_documents,
                        catalogue_connection_lock,
                        heartbeat_message=False,
                    )
                except Exception as exc:
                    repository_metrics.preparation(
                        outcome="failed",
                        duration_seconds=time.perf_counter() - preparation_started,
                    )
                    if is_retryable_catalogue_unavailability(exc):
                        delay = await _mark_catalogue_unavailable(
                            circuit,
                            health_monitor,
                            exc,
                            admission=admission,
                        )
                        logging.warning(
                            "repository ingestion preparation dependency "
                            "unavailable; retaining delivery",
                            exc_info=True,
                        )
                        await message.nak(delay=delay)
                    else:
                        logging.exception(
                            "repository ingestion preparation failed"
                        )
                        repository_metrics.attempt(
                            outcome="failed",
                            queue_seconds=(
                                datetime.now(UTC) - job.enqueued_at
                            ).total_seconds(),
                        )
                        await _retry_or_fail(
                            client,
                            results_store,
                            ingestor,
                            message,
                            job,
                            exc,
                            catalogue_connection_lock,
                        )
                    fetched_messages.pop(id(message), None)
                    continue
                finally:
                    _refresh_lane_client_metrics(lane_metrics, ingestor)
                    lane_metrics.operation_finished()
                repository_metrics.preparation(
                    outcome="succeeded",
                    duration_seconds=time.perf_counter() - preparation_started,
                )
                if _would_exceed_batch(prepared, value, config=config):
                    await flush_prepared(
                        _batch_limit_reason(prepared, value, config=config)
                    )
                jobs.append(job)
                prepared.append(value)
                accepted_messages.append(message)
                fetched_messages.pop(id(message), None)
                if batch_started_at is None:
                    batch_started_at = time.monotonic()
                    heartbeat_task = asyncio.create_task(
                        _heartbeat_messages(accepted_messages)
                    )
                if (
                    batch_started_at is not None
                    and time.monotonic() - batch_started_at
                    >= config.max_wait_seconds
                ):
                    await flush_prepared("max_wait")
                elif _batch_reached_limit(prepared, config=config):
                    await flush_prepared(
                        _batch_reached_reason(prepared, config=config)
                    )
            await cancel_task(fetched_heartbeat_task)
            fetched_heartbeat_task = None
            active_operation_count = 1 if prepared else 0
            lane.active_operation_count = active_operation_count
    finally:
        stop.set()
        await cancel_task(heartbeat_task)
        await cancel_task(fetched_heartbeat_task)
        await cancel_task(dependency_probe_task)
        lane_metrics.operation_started("recovery")
        await _recover_uncommitted_deliveries(
            ingestor,
            prepared=prepared,
            fetched_messages=tuple(fetched_messages.values()),
            accepted_messages=tuple(accepted_messages),
            uncertain_commit=commit_recovery.entered_commit,
            lane_metrics=lane_metrics,
        )
        lane_metrics.operation_finished()
        await _catalogue_call(
            ingestor.close,
            description="catalogue client shutdown",
        )
        await client.drain()


async def _recover_uncommitted_deliveries(
    ingestor,
    *,
    prepared,
    fetched_messages: Sequence[object],
    accepted_messages: Sequence[object],
    uncertain_commit: bool,
    lane_metrics: repository_metrics.IngestionLaneMetrics,
) -> None:
    """Release only deliveries that cannot have entered a DuckLake commit."""

    if uncertain_commit:
        lane_metrics.recovery("uncertain_commit_ack_timeout")
        messages = fetched_messages
    else:
        messages = (*fetched_messages, *accepted_messages)
        if prepared:
            try:
                await asyncio.to_thread(ingestor.discard_prepared, prepared)
            except Exception:
                lane_metrics.recovery("staging_cleanup_failed")
                logging.warning(
                    "repository staging cleanup failed during lane recovery",
                    exc_info=True,
                )

    if not messages:
        return
    results = await asyncio.gather(
        *(message.nak() for message in messages),
        return_exceptions=True,
    )
    failures = sum(isinstance(result, BaseException) for result in results)
    if failures:
        lane_metrics.recovery("shutdown_nak_failed")
        logging.warning(
            "failed to NAK %d of %d uncommitted ingestion deliveries",
            failures,
            len(messages),
        )
    if failures < len(messages):
        lane_metrics.recovery("shutdown_nak")


def _refresh_lane_client_metrics(
    lane_metrics: repository_metrics.IngestionLaneMetrics,
    ingestor,
) -> None:
    """Sample client and token generations without causing a refresh."""

    catalogue = getattr(ingestor, "catalogue", None)
    if catalogue is None:
        return
    lane_metrics.client(
        generation=int(getattr(catalogue, "connection_generation", 1)),
        remints=int(getattr(catalogue, "remint_count", 0)),
    )
    try:
        status, generation = catalogue.token_status
    except Exception:
        status, generation = "unknown", 0
    lane_metrics.token(status=str(status), generation=int(generation))


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


def _batch_limit_reason(
    prepared, value, *, config: IngestionWorkerConfig
) -> str:
    if len(prepared) >= config.max_items:
        return "max_items"
    if (
        sum(item.element_count for item in prepared) + value.element_count
        > config.max_element_rows
    ):
        return "max_element_rows"
    return "max_staged_bytes"


def _batch_reached_reason(prepared, *, config: IngestionWorkerConfig) -> str:
    if len(prepared) >= config.max_items:
        return "max_items"
    if (
        sum(item.element_count for item in prepared)
        >= config.max_element_rows
    ):
        return "max_element_rows"
    return "max_staged_bytes"


async def _commit_batch_isolated(
    client,
    results_store,
    ingestor,
    jobs,
    messages,
    prepared,
    catalogue_connection_lock: asyncio.Lock,
    operation_lease_store,
    *,
    lane_metrics: repository_metrics.IngestionLaneMetrics | None = None,
    recovery_state: _CommitRecoveryState | None = None,
    circuit: IngestionDependencyCircuit | None = None,
    monitor: HealthMonitor | None = None,
    admission: DependencyAdmission | None = None,
) -> None:
    """Commit valid jobs while recursively isolating deterministic poison entries."""

    commit_started = time.perf_counter()
    element_rows = sum(value.element_count for value in prepared)
    staged_bytes = sum(value.staged_bytes for value in prepared)
    try:
        def preflight():
            def attempt():
                return ingestor.preflight_existing_identities(prepared)

            return run_with_catalogue_retry(
                attempt,
                description="repository ingestion identity preflight",
            )

        def commit_fenced():
            def attempt():
                return ingestor.commit_prepared_batch(
                    prepared,
                    cleanup_on_error=False,
                )

            return run_with_catalogue_retry(
                attempt, description="repository ingestion commit"
            )

        async with catalogue_connection_lock:
            existing_identities = await _catalogue_call(
                preflight,
                description="repository ingestion identity preflight",
            )
        operation_ids = _ingestion_commit_operation_ids(
            jobs,
            prepared,
            existing_identities=existing_identities,
        )
        _observe_identity_preflight(
            prepared,
            operation_ids=operation_ids,
        )
        async with operation_leases(
            operation_lease_store,
            operation_ids,
            phase="ingestion-commit",
        ):
            async with catalogue_connection_lock:
                if recovery_state is not None:
                    recovery_state.entered_commit = True
                results = await _catalogue_call(
                    commit_fenced,
                    description="repository ingestion commit",
                )
    except (OperationLeaseUnavailable, OperationLeaseLost):
        repository_metrics.batch(
            outcome="contended",
            duration_seconds=time.perf_counter() - commit_started,
            items=len(prepared),
            element_rows=element_rows,
            staged_bytes=staged_bytes,
        )
        await asyncio.to_thread(ingestor.discard_prepared, prepared)
        if lane_metrics is not None:
            lane_metrics.recovery("lease_contention")
        for message in messages:
            await message.nak(delay=1)
        return
    except Exception as exc:
        if is_retryable_catalogue_unavailability(exc):
            repository_metrics.batch(
                outcome="unavailable",
                duration_seconds=time.perf_counter() - commit_started,
                items=len(prepared),
                element_rows=element_rows,
                staged_bytes=staged_bytes,
            )
            logging.warning(
                "repository ingestion catalogue unavailable; retrying batch",
                exc_info=True,
            )
            await asyncio.to_thread(ingestor.discard_prepared, prepared)
            if lane_metrics is not None:
                lane_metrics.recovery("catalogue_unavailable")
                lane_metrics.circuit("open")
            if circuit is not None and isinstance(
                exc,
                DuckBasinUnavailableError,
            ):
                delay = await _mark_catalogue_unavailable(
                    circuit,
                    monitor,
                    exc,
                    admission=admission,
                )
            else:
                delay = min(
                    30,
                    2
                    ** min(
                        5,
                        max(
                            message.metadata.num_delivered
                            for message in messages
                        )
                        - 1,
                    ),
                )
            for message in messages:
                await message.nak(delay=delay)
            return
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
                operation_lease_store,
                lane_metrics=lane_metrics,
                recovery_state=recovery_state,
                circuit=circuit,
                monitor=monitor,
                admission=admission,
            )
            await _commit_batch_isolated(
                client,
                results_store,
                ingestor,
                jobs[midpoint:],
                messages[midpoint:],
                prepared[midpoint:],
                catalogue_connection_lock,
                operation_lease_store,
                lane_metrics=lane_metrics,
                recovery_state=recovery_state,
                circuit=circuit,
                monitor=monitor,
                admission=admission,
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
                    message,
                    job,
                    exc,
                    catalogue_connection_lock,
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
            messages[0],
            job,
            exc,
            catalogue_connection_lock,
        )
        return

    repository_metrics.batch(
        outcome="succeeded",
        duration_seconds=time.perf_counter() - commit_started,
        items=len(prepared),
        element_rows=element_rows,
        staged_bytes=staged_bytes,
    )
    if lane_metrics is not None:
        lane_metrics.commit_succeeded()
    for job, message, result in zip(jobs, messages, results, strict=True):
        repository_metrics.attempt(
            outcome="succeeded",
            queue_seconds=(datetime.now(UTC) - job.enqueued_at).total_seconds(),
        )
        try:
            await store_ingestion_response(results_store, job=job, result=result)
        except Exception:
            logging.exception("repository ingestion result publication failed")
            await message.nak(delay=1)
            continue
        await message.ack()


def _ingestion_commit_operation_ids(
    jobs: Sequence[IngestionJob],
    prepared: Sequence[PreparedIngestion],
    *,
    existing_identities: ExistingCatalogueIdentities | None = None,
) -> tuple[str, ...]:
    """Fence requests plus identities that the authoritative commit may create."""

    existing = existing_identities or ExistingCatalogueIdentities()
    identities = {f"request:{job.request_id}" for job in jobs}
    for value in prepared:
        if value.document is not None and (
            value.document.document_id not in existing.documents
            or value.replace_projection
        ):
            identities.add(f"document:{value.document.document_id}")
        if (
            value.artifact is not None
            and value.artifact.artifact_id not in existing.artifacts
        ):
            identities.add(f"artifact:{value.artifact.artifact_id}")
    return tuple(sorted(identities))


def _observe_identity_preflight(
    prepared: Sequence[PreparedIngestion],
    *,
    operation_ids: Sequence[str],
) -> None:
    candidates = {
        "document": {
            value.document.document_id
            for value in prepared
            if value.document is not None
        },
        "artifact": {
            value.artifact.artifact_id
            for value in prepared
            if value.artifact is not None
        },
    }
    leased = {
        kind: {
            operation_id.removeprefix(f"{kind}:")
            for operation_id in operation_ids
            if operation_id.startswith(f"{kind}:")
        }
        for kind in candidates
    }
    for kind, values in candidates.items():
        repository_metrics.identity_preflight(
            kind=kind,
            skipped=len(values - leased[kind]),
            leased=len(leased[kind]),
        )


async def _retry_or_fail(
    client,
    results_store,
    ingestor,
    message,
    job: IngestionJob,
    exc: Exception,
    catalogue_connection_lock: asyncio.Lock,
) -> None:
    heartbeat = asyncio.create_task(_heartbeat_messages([message]))
    try:
        await _retry_or_fail_with_heartbeat(
            client,
            results_store,
            ingestor,
            message,
            job,
            exc,
            catalogue_connection_lock,
        )
    finally:
        await cancel_task(heartbeat)


async def _retry_or_fail_with_heartbeat(
    client,
    results_store,
    ingestor,
    message,
    job: IngestionJob,
    exc: Exception,
    catalogue_connection_lock: asyncio.Lock,
) -> None:
    if is_retryable_catalogue_unavailability(exc):
        retry_after = (
            exc.retry_after_seconds
            if isinstance(exc, DuckBasinUnavailableError)
            else None
        )
        await message.nak(
            delay=min(30, max(1, retry_after or 1))
        )
        return
    try:
        processing_failure_count = await record_ingestion_processing_failure(
            results_store, job.request_id
        )
    except Exception:
        logging.warning(
            "repository attempt accounting unavailable; retaining work",
            exc_info=True,
        )
        await message.nak(delay=30)
        return
    if processing_failure_count >= max_delivery_attempts():
        try:
            async with catalogue_connection_lock:
                reconciled = await _catalogue_call(
                    lambda: ingestor.reconcile_crawl_commit(
                        crawl=job.crawl,
                    ),
                    description="repository ingestion failure reconciliation",
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
            error=None if reconciled is not None else _exception_message(exc),
        )
        if durable_state.status == "succeeded":
            await message.ack()
        else:
            await _dead_letter_or_retry(
                client,
                message,
                job,
                durable_state.error or _exception_message(exc),
                durable_state.processing_failure_count,
            )
        return
    await message.nak(
        delay=min(30, 2 ** max(0, processing_failure_count - 1))
    )


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


async def _dead_letter_or_retry(
    client,
    message,
    job: IngestionJob,
    error: str,
    processing_failure_count: int,
) -> None:
    """Only remove terminal work after its durable failure copy is acknowledged."""

    try:
        await publish_dead_letter(
            client.jetstream(),
            job=job,
            error=error,
            processing_failure_count=processing_failure_count,
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
        await asyncio.gather(
            *(message.in_progress() for message in messages),
            return_exceptions=True,
        )
        await asyncio.sleep(interval)


async def _prepare_ingestion_job(
    ingestor,
    message,
    job: IngestionJob,
    known_documents,
    catalogue_connection_lock: asyncio.Lock,
    *,
    heartbeat_message: bool = True,
):
    """Prepare one delivery while heartbeating through durable capacity waits."""

    heartbeat = (
        asyncio.create_task(_heartbeat_messages([message]))
        if heartbeat_message
        else None
    )
    try:
        job_known_documents = known_documents
        if job_known_documents is None:
            job_known_documents = await _known_document_for_crawl(
                ingestor,
                job.crawl,
                catalogue_connection_lock,
            )
        return await asyncio.to_thread(
            ingestor.prepare_from_raw,
            crawl=job.crawl,
            crawl_attempts=job.crawl_attempts,
            crawl_steps=job.crawl_steps,
            known_documents=job_known_documents,
        )
    finally:
        await cancel_task(heartbeat)


async def _known_document_for_crawl(
    ingestor,
    crawl: CrawlRecord,
    catalogue_connection_lock: asyncio.Lock,
) -> dict[str, DocumentRecord]:
    """Read canonical document state without holding the connection during parsing."""

    if crawl.document_id is None:
        return {}
    async with catalogue_connection_lock:
        existing = await _catalogue_call(
            lambda: ingestor.catalogue_service.get_document(
                crawl.document_id
            ),
            description="repository crawl document lookup",
        )
    return {crawl.document_id: existing} if existing is not None else {}


async def _dependency_probe(
    client,
    ingestor,
    catalogue_connection_lock: asyncio.Lock,
    monitor: HealthMonitor,
    lane_metrics: repository_metrics.IngestionLaneMetrics | None = None,
    *,
    circuit: IngestionDependencyCircuit | None = None,
) -> None:
    interval = get_float("ATLAS_INGESTION_WORKER_HEALTH_PROBE_INTERVAL_SECONDS")
    timeout = get_float("ATLAS_INGESTION_WORKER_HEALTH_PROBE_TIMEOUT_SECONDS")
    if interval <= 0 or timeout <= 0:
        monitor.dependencies_unavailable(
            "repository health probe intervals must be greater than zero"
        )
        if lane_metrics is not None:
            lane_metrics.circuit("open")
        return
    while True:
        if not client.is_connected:
            monitor.dependencies_unavailable("NATS is not connected")
            if lane_metrics is not None:
                lane_metrics.circuit("open")
        else:
            if circuit is not None and not await circuit.is_closed():
                await asyncio.sleep(interval)
                continue
            # Waiting for this lane's session-affine connection is not a
            # dependency probe. A healthy commit may own the connection much
            # longer than the readiness-query deadline.
            async with catalogue_connection_lock:
                if lane_metrics is not None:
                    lane_metrics.circuit("half_open")
                validation = asyncio.create_task(
                    _catalogue_call(
                        ingestor.probe,
                        description="catalogue readiness probe",
                    )
                )
                try:
                    await asyncio.wait_for(
                        asyncio.shield(validation),
                        timeout=timeout,
                    )
                except TimeoutError:
                    monitor.dependencies_unavailable(
                        f"catalogue readiness query exceeded {timeout:g}s"
                    )
                    logging.warning(
                        "catalogue readiness query exceeded %.3fs; "
                        "waiting for its owning session without opening the "
                        "process dependency circuit",
                        timeout,
                    )
                    # Remote admission latency is not evidence of a provider
                    # outage. Keep the process-wide circuit closed while this
                    # lane waits for its already-running native call.
                    try:
                        await validation
                    except DuckBasinUnavailableError as exc:
                        monitor.dependencies_unavailable(
                            str(exc) or type(exc).__name__
                        )
                        if circuit is not None:
                            await circuit.unavailable(exc)
                        if lane_metrics is not None:
                            lane_metrics.circuit("open")
                    except Exception as exc:
                        monitor.dependencies_unavailable(
                            str(exc) or type(exc).__name__
                        )
                        if lane_metrics is not None:
                            lane_metrics.circuit("open")
                    else:
                        monitor.dependencies_ready()
                        if lane_metrics is not None:
                            lane_metrics.circuit("closed")
                except DuckBasinUnavailableError as exc:
                    monitor.dependencies_unavailable(
                        str(exc) or type(exc).__name__
                    )
                    if circuit is not None:
                        await circuit.unavailable(exc)
                    if lane_metrics is not None:
                        lane_metrics.circuit("open")
                except Exception as exc:
                    monitor.dependencies_unavailable(
                        str(exc) or type(exc).__name__
                    )
                    if lane_metrics is not None:
                        lane_metrics.circuit("open")
                else:
                    monitor.dependencies_ready()
                    if lane_metrics is not None:
                        lane_metrics.circuit("closed")
            if lane_metrics is not None:
                _refresh_lane_client_metrics(lane_metrics, ingestor)
        await asyncio.sleep(interval)


class _CatalogueHardHangError(BaseException):
    """Fallback for tests if the production hard-exit hook returns."""


async def _catalogue_call(
    operation,
    *args,
    description: str,
    hard_timeout_seconds: float = INGESTION_CATALOGUE_HARD_TIMEOUT_SECONDS,
):
    """Run one native catalogue call and terminate if it cannot unwind."""

    if hard_timeout_seconds <= 0:
        raise ValueError("catalogue hard timeout must be greater than zero")
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.wait_for(
            asyncio.shield(task),
            timeout=hard_timeout_seconds,
        )
    except TimeoutError:
        _terminate_for_catalogue_hang(description, hard_timeout_seconds)
        raise _CatalogueHardHangError(description)


def _terminate_for_catalogue_hang(
    description: str,
    timeout_seconds: float,
) -> Never:
    logging.critical(
        "%s remained stuck for %.3fs; terminating ingestion worker",
        description,
        timeout_seconds,
    )
    os._exit(70)


async def _mark_catalogue_unavailable(
    circuit: IngestionDependencyCircuit,
    monitor: HealthMonitor | None,
    exc: DuckBasinUnavailableError,
    *,
    admission: DependencyAdmission | None = None,
) -> float:
    delay = await circuit.unavailable(exc, admission=admission)
    if monitor is not None:
        monitor.dependencies_unavailable(str(exc) or type(exc).__name__)
    return delay
