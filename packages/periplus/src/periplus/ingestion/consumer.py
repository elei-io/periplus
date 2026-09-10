"""Evidence-only DuckLake ingestion loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from contextlib import AsyncExitStack
from datetime import UTC, datetime
import logging
import os
import random
import time
from typing import Never

from nats.errors import TimeoutError as NatsTimeoutError

from periplus.retention.identities import WriteClaimUnavailable
from periplus.platform.config import get_float
from periplus.platform.config.performance import INGESTION_CATALOGUE_HARD_TIMEOUT_SECONDS
from periplus.ingestion import metrics as repository_metrics
from periplus.platform.catalogue import (
    CatalogueConflictError,
)
from periplus.platform.catalogue.operations import (
    is_retryable_catalogue_unavailability,
    run_with_catalogue_retry,
)
from periplus.platform.health import HealthMonitor
from periplus.ingestion.pipeline import IngestionWorkerConfig
from periplus.ingestion.queue import (
    DURABLE,
    STREAM,
    IngestionJob,
    ack_wait_seconds,
    ensure_pending_ingestion,
    get_ingestion_state,
    max_delivery_attempts,
    publish_dead_letter,
    record_ingestion_processing_failure,
    store_ingestion_response,
)
from periplus.ingestion.service import PreparedIngestion, repository_ingestor_from_env
from periplus.platform.messaging.catalogue_workers import CatalogueLaneReporter
from periplus.platform.messaging.leases import (
    OperationLeaseBackendUnavailable,
    OperationLeaseLost,
    OperationLeaseUnavailable,
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
        identities = {f"ingestion:{job.request_id}" for job in self.jobs}
        for job in self.jobs:
            if job.visit:
                identities.add(f"observation:{job.visit.visit.visit_id}")
                if job.visit.document:
                    identities.add(f"content:{job.visit.document.content_sha256}")
            if job.lineage:
                if getattr(job.lineage, "collection_id", None):
                    identities.add(f"collection:{job.lineage.collection_id}")
                if getattr(job.lineage, "observation_id", None):
                    identities.add(f"observation:{job.lineage.observation_id}")
        return tuple(sorted(identities))


async def run(
    *,
    stop: asyncio.Event,
    monitor: HealthMonitor,
    jetstream,
    results_store,
    leases,
    subscription,
    lane: CatalogueLaneReporter | None = None,
    lane_index: int = 0,
) -> None:
    lane = lane or CatalogueLaneReporter(lane_index=lane_index)
    lane.attach(lambda: monitor.status(include_liveness=False))
    metrics = repository_metrics.IngestionLaneMetrics(lane_index)
    config = IngestionWorkerConfig.defaults()
    ingestor = None
    try:
        ingestor = await _catalogue_call(
            repository_ingestor_from_env,
            description="ingestion catalogue connection",
        )
        await _catalogue_call(
            ingestor.validate,
            description="ingestion catalogue validation",
        )
        monitor.dependencies_ready()
        monitor.subsystem_ready("ingestion")

        while not stop.is_set():
            monitor.heartbeat()
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
                jetstream=jetstream,
                results_store=results_store,
                ingestor=ingestor,
                leases=leases,
                messages=messages,
                lane=lane,
                metrics=metrics,
            )
            await _observe_queue(jetstream, monitor)
    finally:
        if ingestor is not None:
            await _catalogue_call(
                ingestor.close,
                description="ingestion catalogue client close",
            )


async def _process_messages(
    *,
    jetstream,
    results_store,
    ingestor,
    leases,
    messages,
    lane: CatalogueLaneReporter,
    metrics: repository_metrics.IngestionLaneMetrics,
) -> None:
    heartbeat_messages = list(messages)
    heartbeat = asyncio.create_task(_heartbeat_messages(heartbeat_messages))
    try:
        batch = await _prepare_batch(
            jetstream=jetstream,
            results_store=results_store,
            ingestor=ingestor,
            messages=messages,
            metrics=metrics,
        )
        heartbeat_messages[:] = batch.messages
        if not batch.evidence:
            return

        lane.active_operation_count += 1
        metrics.operation_started("commit")
        started = time.perf_counter()
        try:
            async with AsyncExitStack() as claims:
                batch = await _claim_batch(batch, leases, claims, metrics)
                heartbeat_messages[:] = batch.messages
                if not batch.evidence:
                    return
                while batch.evidence:
                    results = await _catalogue_call(
                        _commit_prepared_batch,
                        ingestor,
                        batch.evidence,
                        metrics,
                        description="ingestion evidence commit",
                    )
                    if not isinstance(results, WriteClaimUnavailable):
                        break
                    batch = await _defer_write_claims(batch, results, metrics)
                    heartbeat_messages[:] = batch.messages
                if not batch.evidence:
                    return
        except (OperationLeaseUnavailable, OperationLeaseLost):
            for message in batch.messages:
                await message.nak(delay=1)
            return
        except Exception as exc:
            repository_metrics.batch(
                outcome="failed",
                duration_seconds=time.perf_counter() - started,
                items=len(batch.evidence),
            )
            for message, job in zip(
                batch.messages,
                batch.jobs,
                strict=True,
            ):
                await _retry_or_fail(
                    jetstream,
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


async def _claim_batch(batch, leases, claims, metrics) -> PreparedBatch:
    """Reserve each job independently, sharing identities already owned by this batch."""
    selected = PreparedBatch()
    owned: set[str] = set()
    seen: set[str] = set()
    for message, job, evidence in zip(batch.messages, batch.jobs, batch.evidence, strict=True):
        if job.request_id in seen:
            await message.nak(delay=5)
            metrics.recovery("duplicate_delivery")
            continue
        seen.add(job.request_id)
        candidate = PreparedBatch([message], [job], [evidence])
        needed = set(candidate.operation_ids) - owned
        try:
            await claims.enter_async_context(operation_leases(
                leases, needed, phase="ingestion", acquire_timeout=0,
            ))
        except OperationLeaseUnavailable:
            await message.nak(delay=5)
            metrics.recovery("operation_busy")
            continue
        owned.update(needed)
        selected.append(message, job, evidence)
    return selected


async def _prepare_batch(
    *,
    jetstream,
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
        except Exception:
            logging.exception("invalid ingestion envelope")
            await message.term()
            continue
        try:
            state = await ensure_pending_ingestion(results_store, job=job)
        except Exception:
            logging.exception("ingestion state unavailable")
            await message.nak(delay=5)
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
                jetstream,
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
    jetstream,
    results_store,
    ingestor,
    message,
    job: IngestionJob,
    exc: Exception,
) -> None:
    if isinstance(exc, OperationLeaseBackendUnavailable) or is_retryable_catalogue_unavailability(exc):
        await message.nak(delay=1)
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
            jetstream,
            job=job,
            error=state.error or _exception_message(exc),
            processing_failure_count=state.processing_failure_count,
        )
    except Exception:
        logging.warning("ingestion dead-letter publication failed", exc_info=True)
        await message.nak(delay=30)
    else:
        await message.term()


async def _defer_write_claims(
    batch: PreparedBatch, conflict: WriteClaimUnavailable,
    metrics: repository_metrics.IngestionLaneMetrics,
) -> PreparedBatch:
    selected = PreparedBatch()
    blocked_ids = {f"{kind}:{identity}" for kind, identity in conflict.blocked_until}
    for message, job, evidence in zip(batch.messages, batch.jobs, batch.evidence, strict=True):
        candidate = PreparedBatch([message], [job], [evidence])
        overlapping = set(candidate.operation_ids) & blocked_ids
        if not blocked_ids or overlapping:
            expiries = [expiry for (kind, identity), expiry in conflict.blocked_until.items()
                        if f"{kind}:{identity}" in overlapping]
            remaining = max((expiry - datetime.now(UTC)).total_seconds() for expiry in expiries) if expiries else 5
            # Released claims need no ten-minute delay; abandoned claims must not
            # consume a worker while waiting. Jitter spreads durable redeliveries.
            await message.nak(delay=max(1, min(30, remaining)) + random.uniform(0, 1))
            metrics.recovery("write_claim_busy")
        else:
            selected.append(message, job, evidence)
    if len(selected.jobs) == len(batch.jobs):
        raise RuntimeError("write claim conflict did not match any ingestion job")
    return selected


def _commit_prepared_batch(
    ingestor,
    evidence: list[PreparedIngestion],
    metrics: repository_metrics.IngestionLaneMetrics,
):
    def commit():
        try:
            return ingestor.commit_prepared_batch(evidence)
        except WriteClaimUnavailable as conflict:
            # Return the rejection to the async consumer rather than sleeping
            # through catalogue retries with the whole batch held.
            return conflict

    return run_with_catalogue_retry(
        commit,
        description="ingestion evidence commit",
        on_conflict=lambda: metrics.recovery("commit_conflict"),
    )


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
        stalled_after_seconds=get_float("PERIPLUS_WORKER_QUEUE_STALL_SECONDS"),
    )
    repository_metrics.queue_state(
        pending=int(info.num_pending or 0),
        ack_pending=int(info.num_ack_pending or 0),
        redelivered=int(info.num_redelivered or 0),
        oldest_pending_age_seconds=age,
        stalled=(
            pending > 0
            and age > get_float("PERIPLUS_WORKER_QUEUE_STALL_SECONDS")
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


def _exception_message(exc: BaseException) -> str:
    messages: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        message = str(current).strip() or type(current).__name__
        if not messages or message != messages[-1]:
            messages.append(message)
        current = current.__cause__
    return ": ".join(messages)


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
        "%s remained stuck for %.3fs; terminating ingestor",
        description,
        timeout_seconds,
    )
    os._exit(70)
