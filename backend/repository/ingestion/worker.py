"""Evidence-only DuckLake ingestion loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
import os
import time
from typing import Never

from nats.errors import TimeoutError as NatsTimeoutError

from config import get_float
from config.performance import INGESTION_CATALOGUE_HARD_TIMEOUT_SECONDS
from observability import repository_metrics
from repository.catalogue import (
    CatalogueConflictError,
    DuckBasinUnavailableError,
    ServiceAccountTokenProvider,
)
from repository.catalogue.operations import is_retryable_catalogue_unavailability
from repository.ingestion.health import HealthMonitor
from repository.ingestion.pipeline import IngestionWorkerConfig
from repository.ingestion.queue import (
    DURABLE,
    STREAM,
    SUBJECT,
    IngestionJob,
    ack_wait_seconds,
    ensure_dead_letter_stream,
    ensure_ingestion_results,
    ensure_pending_ingestion,
    ensure_repository_consumer,
    ensure_repository_stream,
    get_ingestion_state,
    max_delivery_attempts,
    publish_dead_letter,
    record_ingestion_processing_failure,
    store_ingestion_response,
)
from repository.ingestion.recovery import IngestionDependencyCircuit
from repository.service import PreparedIngestion, repository_ingestor_from_env
from runtime.catalogue_workers import CatalogueLaneReporter
from runtime.nats_client import connect_nats
from runtime.operation_leases import (
    OperationLeaseLost,
    OperationLeaseUnavailable,
    ensure_operation_lease_storage,
    operation_leases,
)


@dataclass(slots=True)
class PreparedBatch:
    """Keep each source delivery beside the job and evidence it produced."""

    messages: list[object] = field(default_factory=list)
    jobs: list[IngestionJob] = field(default_factory=list)
    evidence: list[PreparedIngestion] = field(default_factory=list)

    def append(
        self,
        message: object,
        job: IngestionJob,
        prepared: PreparedIngestion,
    ) -> None:
        self.messages.append(message)
        self.jobs.append(job)
        self.evidence.append(prepared)

    @property
    def operation_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(f"ingestion:{job.request_id}" for job in self.jobs)
        )


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
    lane.attach(lambda: monitor.status(include_liveness=False))
    metrics = repository_metrics.IngestionLaneMetrics(lane_index)
    config = IngestionWorkerConfig.defaults()
    client = await connect_nats()
    ingestor = None
    try:
        jetstream = client.jetstream()
        await ensure_repository_stream(jetstream)
        await ensure_dead_letter_stream(jetstream)
        results_store = await ensure_ingestion_results(jetstream)
        leases = await ensure_operation_lease_storage(jetstream)
        await ensure_repository_consumer(jetstream)
        subscription = await jetstream.pull_subscribe(
            SUBJECT,
            durable=DURABLE,
            stream=STREAM,
        )
        admission = await circuit.admit(lane_index, stop=stop)
        while ingestor is None and admission is not None and not stop.is_set():
            try:
                ingestor = await _catalogue_call(
                    repository_ingestor_from_env,
                    tokens=tokens,
                    description="ingestion catalogue client creation",
                )
                await _catalogue_call(
                    ingestor.validate,
                    description="ingestion catalogue validation",
                )
            except DuckBasinUnavailableError as exc:
                delay = await circuit.unavailable(exc, admission=admission)
                monitor.dependencies_unavailable(str(exc) or type(exc).__name__)
                metrics.circuit("open")
                await _wait_or_stop(stop, delay)
                admission = await circuit.admit(lane_index, stop=stop)
        if ingestor is None or admission is None:
            return
        if admission.probe_required:
            await circuit.recovered(admission, lane_index)
        _refresh_lane_metrics(metrics, ingestor)
        metrics.circuit(await circuit.state())
        monitor.dependencies_ready()
        monitor.subsystem_ready("ingestion")

        while not stop.is_set():
            monitor.heartbeat()
            admission = await circuit.admit(lane_index, stop=stop)
            if admission is None:
                break
            if admission.probe_required:
                try:
                    await _catalogue_call(
                        ingestor.probe,
                        description="ingestion catalogue recovery probe",
                    )
                except DuckBasinUnavailableError as exc:
                    await circuit.unavailable(exc, admission=admission)
                    monitor.dependencies_unavailable(
                        str(exc) or type(exc).__name__
                    )
                    metrics.circuit("open")
                    continue
                await circuit.recovered(admission, lane_index)
                monitor.dependencies_ready()
            metrics.circuit(await circuit.state())
            try:
                messages = await subscription.fetch(
                    batch=config.max_items,
                    timeout=config.max_wait_seconds,
                )
            except (NatsTimeoutError, TimeoutError):
                await _observe_queue(jetstream, monitor)
                continue
            if not messages:
                continue
            await _process_messages(
                client=client,
                results_store=results_store,
                ingestor=ingestor,
                leases=leases,
                messages=messages,
                lane=lane,
                metrics=metrics,
                circuit=circuit,
                admission=admission,
            )
            _refresh_lane_metrics(metrics, ingestor)
            await _observe_queue(jetstream, monitor)
    finally:
        if ingestor is not None:
            await _catalogue_call(
                ingestor.close,
                description="ingestion catalogue client close",
            )
        await client.drain()


async def _process_messages(
    *,
    client,
    results_store,
    ingestor,
    leases,
    messages,
    lane: CatalogueLaneReporter,
    metrics: repository_metrics.IngestionLaneMetrics,
    circuit: IngestionDependencyCircuit,
    admission,
) -> None:
    heartbeat = asyncio.create_task(_heartbeat_messages(messages))
    try:
        batch = await _prepare_batch(
            client=client,
            results_store=results_store,
            ingestor=ingestor,
            messages=messages,
            metrics=metrics,
        )
        if not batch.evidence:
            return

        lane.active_operation_count += 1
        metrics.operation_started("commit")
        started = time.perf_counter()
        try:
            async with operation_leases(
                leases,
                batch.operation_ids,
                phase="ingestion",
                acquire_timeout=0,
            ):
                results = await _catalogue_call(
                    ingestor.commit_prepared_batch,
                    batch.evidence,
                    description="ingestion evidence commit",
                )
        except (OperationLeaseUnavailable, OperationLeaseLost):
            for message in batch.messages:
                await message.nak(delay=1)
            return
        except DuckBasinUnavailableError as exc:
            delay = await circuit.unavailable(exc, admission=admission)
            for message in batch.messages:
                await message.nak(delay=delay)
            return
        except Exception as exc:
            repository_metrics.batch(
                outcome="failed",
                duration_seconds=time.perf_counter() - started,
                items=len(batch.evidence),
                element_rows=0,
                staged_bytes=0,
            )
            for message, job in zip(
                batch.messages,
                batch.jobs,
                strict=True,
            ):
                await _retry_or_fail(
                    client,
                    results_store,
                    ingestor,
                    message,
                    job,
                    exc,
                )
            return
        finally:
            lane.active_operation_count -= 1
            metrics.operation_finished()
        repository_metrics.batch(
            outcome="succeeded",
            duration_seconds=time.perf_counter() - started,
            items=len(batch.evidence),
            element_rows=0,
            staged_bytes=0,
        )
        metrics.commit_succeeded()
        for message, job, result in zip(
            batch.messages,
            batch.jobs,
            results,
            strict=True,
        ):
            repository_metrics.attempt(
                outcome="succeeded",
                queue_seconds=(
                    datetime.now(UTC) - job.enqueued_at
                ).total_seconds(),
            )
            try:
                await store_ingestion_response(
                    results_store,
                    job=job,
                    result=result,
                )
            except Exception:
                logging.exception("ingestion result publication failed")
                await message.nak(delay=1)
            else:
                await message.ack()
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


async def _prepare_batch(
    *,
    client,
    results_store,
    ingestor,
    messages,
    metrics: repository_metrics.IngestionLaneMetrics,
) -> PreparedBatch:
    """Validate deliveries, settle completed jobs, and prepare pending work."""

    batch = PreparedBatch()
    for message in messages:
        try:
            job = IngestionJob.model_validate_json(message.data)
            state = await ensure_pending_ingestion(results_store, job=job)
        except Exception:
            logging.exception("invalid ingestion envelope")
            await message.term()
            continue
        if state.status == "succeeded":
            await message.ack()
            continue
        if state.status == "failed":
            await message.term()
            continue
        try:
            metrics.operation_started("prepare")
            evidence = await asyncio.to_thread(ingestor.prepare, job)
            repository_metrics.preparation(
                outcome="succeeded",
                duration_seconds=0,
            )
        except Exception as exc:
            repository_metrics.preparation(
                outcome="failed",
                duration_seconds=0,
            )
            await _retry_or_fail(
                client,
                results_store,
                ingestor,
                message,
                job,
                exc,
            )
            continue
        finally:
            metrics.operation_finished()
        batch.append(message, job, evidence)
    return batch


async def _retry_or_fail(
    client,
    results_store,
    ingestor,
    message,
    job: IngestionJob,
    exc: Exception,
) -> None:
    if is_retryable_catalogue_unavailability(exc):
        delay = (
            exc.retry_after_seconds
            if isinstance(exc, DuckBasinUnavailableError)
            else 1
        )
        await message.nak(delay=min(30, max(1, delay or 1)))
        return
    count = await record_ingestion_processing_failure(
        results_store,
        job.request_id,
    )
    if count < max_delivery_attempts():
        await message.nak(delay=min(30, 2 ** max(0, count - 1)))
        return
    try:
        reconciled = await _catalogue_call(
            ingestor.reconcile_commit,
            job,
            description="ingestion failure reconciliation",
        )
    except CatalogueConflictError as conflict:
        exc = conflict
        reconciled = None
    except Exception:
        logging.warning(
            "ingestion failure reconciliation unavailable",
            exc_info=True,
        )
        await message.nak(delay=30)
        return
    error = None if reconciled is not None else _exception_message(exc)
    state = await store_ingestion_response(
        results_store,
        job=job,
        result=reconciled,
        error=error,
    )
    if state.status == "succeeded":
        await message.ack()
        return
    try:
        await publish_dead_letter(
            client.jetstream(),
            job=job,
            error=state.error or _exception_message(exc),
            processing_failure_count=state.processing_failure_count,
        )
    except Exception:
        logging.warning("ingestion dead-letter publication failed", exc_info=True)
        await message.nak(delay=30)
    else:
        await message.term()


async def _observe_queue(jetstream, monitor: HealthMonitor) -> None:
    info = await jetstream.consumer_info(STREAM, DURABLE)
    pending = int(info.num_pending or 0) + int(info.num_ack_pending or 0)
    marker = (
        int(info.delivered.stream_seq or 0),
        int(info.ack_floor.stream_seq or 0),
    )
    age = monitor.queue_observed(
        "ingestion",
        pending=pending,
        progress_marker=marker,
        stalled_after_seconds=get_float("ATLAS_WORKER_QUEUE_STALL_SECONDS"),
    )
    repository_metrics.queue_state(
        pending=int(info.num_pending or 0),
        ack_pending=int(info.num_ack_pending or 0),
        redelivered=int(info.num_redelivered or 0),
        oldest_pending_age_seconds=age,
        stalled=(
            pending > 0
            and age > get_float("ATLAS_WORKER_QUEUE_STALL_SECONDS")
        ),
    )


async def _heartbeat_messages(messages) -> None:
    interval = max(1.0, min(30.0, ack_wait_seconds() / 3))
    while True:
        await asyncio.gather(
            *(message.in_progress() for message in messages),
            return_exceptions=True,
        )
        await asyncio.sleep(interval)


def _refresh_lane_metrics(
    metrics: repository_metrics.IngestionLaneMetrics,
    ingestor,
) -> None:
    metrics.client(
        generation=ingestor.catalogue.connection_generation,
        remints=ingestor.catalogue.remint_count,
    )
    token_status, token_generation = ingestor.catalogue.token_status
    metrics.token(status=token_status, generation=token_generation)


def _exception_message(exc: BaseException) -> str:
    messages: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        message = str(current).strip() or type(current).__name__
        if not messages or message != messages[-1]:
            messages.append(message)
        current = current.__cause__
    return ": ".join(messages)


async def _wait_or_stop(stop: asyncio.Event, delay: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except TimeoutError:
        pass


class _CatalogueHardHangError(BaseException):
    pass


async def _catalogue_call(
    operation,
    *args,
    description: str,
    hard_timeout_seconds: float = INGESTION_CATALOGUE_HARD_TIMEOUT_SECONDS,
    **kwargs,
):
    if hard_timeout_seconds <= 0:
        raise ValueError("catalogue hard timeout must be greater than zero")
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
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
