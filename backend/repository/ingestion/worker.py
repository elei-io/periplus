"""Catalogue ingestion loop owned by the catalog worker process."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from datetime import UTC, datetime

from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.errors import BucketNotFoundError, KeyDeletedError, KeyNotFoundError

from repository.catalogue import CatalogueConflictError, CatalogueValidationError
from observability import materialization_metrics, repository_metrics
from prometheus_client import start_http_server
from config import get_bool, get_float, get_int, get_str
from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_views.models import CatalogueViewReference
from control.catalogue_queries.models import CatalogueQuery, CatalogueQueryRevision
from db.session import session_scope
from materialization.commit import commit_scope, commit_scope_batch, record_scope_failure
from repository.catalogue.fanout import CrawlMaterializationFanoutStore
from materialization.queue import (
    COMMIT_DURABLE,
    COMMIT_STREAM,
    COMMIT_SUBJECT,
    SCOPE_LIVE_SUBJECT,
    DEAD_LETTER_SUBJECT,
    MaterializationDeadLetter,
    MaterializationCommitJob,
    MaterializationFailureJob,
    CrawlMaterializationFanoutPlanJob,
    ensure_streams as ensure_materialization_streams,
)
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
from repository.catalogue.materializations import MaterializationStore
from repository.catalogue.operations import operation_lock, operation_locks
from sqlalchemy import select
from runtime.maintenance_queue import MAINTENANCE_LEASE_BUCKET
from runtime.graph_queue import READINESS_SUBJECT, ReadinessWork
from runtime.navigation import (
    navigation_event_id,
    navigation_object_name,
    put_navigation_package,
)
from runtime.navigation_contract import NavigationPackage


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


async def run(
    initialized: asyncio.Event | None = None, monitor: HealthMonitor | None = None
) -> None:
    stop = asyncio.Event()
    config = IngestionWorkerConfig.from_env()
    client = await connect_repository_nats()
    jetstream = client.jetstream()
    try:
        maintenance_leases = await jetstream.key_value(MAINTENANCE_LEASE_BUCKET)
    except BucketNotFoundError:
        maintenance_leases = None
    await ensure_materialization_streams(jetstream)
    await ensure_repository_stream(jetstream)
    await ensure_dead_letter_stream(jetstream)
    results_store = await ensure_ingestion_results(jetstream)
    await ensure_repository_consumer(jetstream)
    subscription = await jetstream.pull_subscribe(
        SUBJECT,
        durable=DURABLE,
        stream=STREAM,
    )
    materialization_subscription = await jetstream.pull_subscribe(
        COMMIT_SUBJECT,
        durable=COMMIT_DURABLE,
        stream=COMMIT_STREAM,
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
            get_int("ATLAS_CATALOG_WORKER_METRICS_PORT"),
            addr=get_str("ATLAS_METRICS_HOST"),
        )
    health_monitor = monitor or HealthMonitor(
        heartbeat_timeout_seconds=float(
            get_str("ATLAS_CATALOG_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS")
        )
    )
    health_monitor.dependencies_ready()
    health_monitor.subsystem_ready("ingestion")
    health_monitor.subsystem_ready("materialization_commit")
    health_server, _health_thread = start_health_server(
        address=get_str("ATLAS_CATALOG_WORKER_HEALTH_HOST"),
        port=get_int("ATLAS_CATALOG_WORKER_HEALTH_PORT"),
        monitor=health_monitor,
    )
    if initialized is not None:
        initialized.set()
    health_heartbeat_task = asyncio.create_task(_health_heartbeat(health_monitor))
    dependency_probe_task = asyncio.create_task(
        _dependency_probe(client, health_ingestor, health_monitor)
    )
    heartbeat_task = None
    catalogue_write_lock = asyncio.Lock()
    materialization_commit_task = asyncio.create_task(
        _consume_materialization_commits(
            jetstream,
            materialization_subscription,
            ingestor.catalogue,
            catalogue_write_lock,
            maintenance_leases,
            stop,
        ),
        name="catalog-materialization-commits",
    )
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
            catalogue_write_lock,
            navigation_store,
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
            if await _maintenance_active(maintenance_leases):
                await asyncio.sleep(0.25)
                continue
            if (
                prepared
                and batch_started_at is not None
                and time.monotonic() - batch_started_at >= config.max_wait_seconds
            ):
                await flush_prepared()
                continue
            await asyncio.to_thread(
                _dematerialize_requested_materialization, ingestor.catalogue
            )
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
                            if durable_state.navigation is not None:
                                await _publish_navigation_readiness(
                                    client, job, durable_state.navigation
                                )
                            await _notify(client, job, durable_state)
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
                        client,
                        results_store,
                        ingestor,
                        navigation_store,
                        message,
                        job,
                        exc,
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
        await _cancel_task(materialization_commit_task)
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
    argparse.ArgumentParser(description="Run the Atlas catalog ingestion loop.").parse_args()
    logging.basicConfig(
        level=get_str("ATLAS_LOG_LEVEL"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


async def _commit_materialization_if_ready(jetstream, subscription, catalogue) -> None:
    try:
        messages = await subscription.fetch(batch=1, timeout=0.01)
    except (NatsTimeoutError, asyncio.TimeoutError):
        return
    for message in messages:
        heartbeat = asyncio.create_task(_heartbeat_messages([message]))
        try:
            payload = json.loads(message.data)
            if payload.get("kind") == "fanout_plan":
                plan = CrawlMaterializationFanoutPlanJob.model_validate(payload)
                failure = None
                job = None
            elif payload.get("kind") == "failure":
                failure = MaterializationFailureJob.model_validate(payload)
                job = None
                plan = None
            else:
                job = MaterializationCommitJob.model_validate(payload)
                failure = None
                plan = None
        except Exception:
            logging.exception("discarding invalid materialization commit job")
            await message.term()
            await _cancel_task(heartbeat)


        if plan is not None:
            try:
                await _commit_fanout_plan(jetstream, catalogue, plan)
            except Exception:
                logging.exception("failed to persist crawl materialization fan-out plan")
                await message.nak(delay=30)
            else:
                await message.ack()
            finally:
                await _cancel_task(heartbeat)
            continue
        if failure is not None:
            try:
                def record_failure_fenced():
                    with operation_lock(failure.scope.operation_id):
                        return record_scope_failure(catalogue, failure)

                recorded = await asyncio.to_thread(record_failure_fenced)
                if recorded:
                    await _settle_crawl_fanouts(jetstream, catalogue, failure.scope)
            except Exception:
                logging.exception("failed to persist materialization scope failure")
                await message.nak(delay=30)
            else:
                await message.ack()
            finally:
                await _cancel_task(heartbeat)
            continue
        assert job is not None
        commit_started = time.perf_counter()
        try:
            def commit_scope_fenced():
                with operation_lock(job.scope.operation_id):
                    return commit_scope(catalogue, job)

            await asyncio.to_thread(commit_scope_fenced)
        except Exception as exc:
            materialization_metrics.operation(
                phase="commit",
                outcome="failed",
                duration_seconds=time.perf_counter() - commit_started,
            )
            logging.exception(
                "materialization commit failed for operation %s",
                job.scope.operation_id,
            )
            deliveries = message.metadata.num_delivered
            maximum = get_int("ATLAS_MATERIALIZATION_MAX_DELIVER")
            if deliveries < maximum:
                await message.nak(delay=5)
                continue
            failure = MaterializationFailureJob(
                scope=job.scope,
                error=str(exc),
                started_at=job.started_at,
                completed_at=datetime.now(UTC),
            )
            dead_letter = MaterializationDeadLetter(
                job=job.scope,
                stage="commit",
                error=str(exc),
                delivery_count=deliveries,
                failed_at=datetime.now(UTC),
                staging_key=job.staging_key,
            )
            try:
                await asyncio.to_thread(record_scope_failure, catalogue, failure)
                await jetstream.publish(
                    DEAD_LETTER_SUBJECT,
                    dead_letter.model_dump_json().encode(),
                    headers={
                        "Nats-Msg-Id": f"{job.scope.operation_id}-commit-dead"
                    },
                )
            except Exception:
                logging.exception("failed to persist materialization dead letter")
                await message.nak(delay=30)
            else:
                await message.term()
        else:
            materialization_metrics.operation(
                phase="commit",
                outcome="succeeded",
                duration_seconds=time.perf_counter() - commit_started,
            )
            await _settle_crawl_fanouts(jetstream, catalogue, job.scope)
            await message.ack()
        finally:
            await _cancel_task(heartbeat)


async def _consume_materialization_commits(
    jetstream,
    subscription,
    catalogue,
    catalogue_write_lock: asyncio.Lock,
    maintenance_leases,
    stop: asyncio.Event,
) -> None:
    """Batch compatible analytical appends with a bounded oldest-item deadline."""

    max_items = get_int("ATLAS_MATERIALIZATION_COMMIT_BATCH_ITEMS")
    max_bytes = get_int("ATLAS_MATERIALIZATION_COMMIT_BATCH_BYTES")
    max_wait = get_float("ATLAS_MATERIALIZATION_COMMIT_BATCH_WAIT_SECONDS")
    if max_items <= 0 or max_bytes <= 0 or max_wait <= 0:
        raise ValueError("materialization commit batch limits must be positive")
    buffers: dict[tuple, list[tuple[object, MaterializationCommitJob, asyncio.Task]]] = {}
    started: dict[tuple, float] = {}
    next_queue_snapshot = 0.0
    try:
        while not stop.is_set():
            if await _maintenance_active(maintenance_leases):
                await asyncio.sleep(0.25)
                continue
            now = time.monotonic()
            due = [
                key
                for key, entries in buffers.items()
                if _materialization_batch_due(
                    entries,
                    started_at=started[key],
                    now=now,
                    max_items=max_items,
                    max_bytes=max_bytes,
                    max_wait=max_wait,
                )
            ]
            for key in due:
                entries = buffers.pop(key)
                started.pop(key, None)
                async with catalogue_write_lock:
                    await _commit_materialization_entries(
                        jetstream, catalogue, entries
                    )
            if due:
                continue
            if time.monotonic() >= next_queue_snapshot:
                try:
                    info = await jetstream.consumer_info(COMMIT_STREAM, COMMIT_DURABLE)
                    materialization_metrics.queue_state(
                        phase="commit",
                        pending=info.num_pending,
                        ack_pending=info.num_ack_pending,
                    )
                except Exception:
                    logging.warning(
                        "materialization commit queue metrics unavailable",
                        exc_info=True,
                    )
                next_queue_snapshot = time.monotonic() + 5
            timeout = 1.0
            if started:
                timeout = max(
                    0.001,
                    min(started_at + max_wait for started_at in started.values())
                    - time.monotonic(),
                )
            try:
                messages = await subscription.fetch(batch=max_items, timeout=timeout)
            except (NatsTimeoutError, asyncio.TimeoutError):
                continue
            for message in messages:
                heartbeat = asyncio.create_task(_heartbeat_messages([message]))
                try:
                    payload = json.loads(message.data)
                    kind = payload.get("kind")
                    if kind == "fanout_plan":
                        plan = CrawlMaterializationFanoutPlanJob.model_validate(payload)
                        try:
                            async with catalogue_write_lock:
                                await _commit_fanout_plan(jetstream, catalogue, plan)
                        except Exception:
                            logging.exception(
                                "failed to persist crawl materialization fan-out plan"
                            )
                            await message.nak(delay=30)
                        else:
                            await message.ack()
                        await _cancel_task(heartbeat)
                        continue
                    if kind == "failure":
                        failure = MaterializationFailureJob.model_validate(payload)
                        try:
                            async with catalogue_write_lock:
                                recorded = await asyncio.to_thread(
                                    record_scope_failure, catalogue, failure
                                )
                                if recorded:
                                    await _settle_crawl_fanouts(
                                        jetstream, catalogue, failure.scope
                                    )
                        except Exception:
                            logging.exception(
                                "failed to persist materialization scope failure"
                            )
                            await message.nak(delay=30)
                        else:
                            await message.ack()
                        await _cancel_task(heartbeat)
                        continue
                    job = MaterializationCommitJob.model_validate(payload)
                except Exception:
                    logging.exception("discarding invalid materialization commit job")
                    await message.term()
                    await _cancel_task(heartbeat)
                    continue
                key = (
                    job.scope.materialization_id,
                    job.scope.definition_revision_id,
                    job.scope.target_table,
                    job.scope.scope_kind,
                    job.scope.scope_column,
                    job.scope.source,
                )
                entries = buffers.setdefault(key, [])
                if any(value.scope.scope_id == job.scope.scope_id for _, value, _ in entries):
                    async with catalogue_write_lock:
                        await _commit_materialization_entries(
                            jetstream, catalogue, buffers.pop(key)
                        )
                    started.pop(key, None)
                    entries = buffers.setdefault(key, [])
                entries.append((message, job, heartbeat))
                started.setdefault(key, time.monotonic())
    finally:
        for entries in buffers.values():
            for _message, _job, heartbeat in entries:
                await _cancel_task(heartbeat)


def _materialization_batch_due(
    entries,
    *,
    started_at: float,
    now: float,
    max_items: int,
    max_bytes: int,
    max_wait: float,
) -> bool:
    return bool(entries) and (
        len(entries) >= max_items
        or sum(job.file_bytes for _message, job, _heartbeat in entries) >= max_bytes
        or now - started_at >= max_wait
    )


async def _commit_materialization_entries(jetstream, catalogue, entries) -> None:
    """Commit a compatible batch, recursively isolating a bad staged scope."""

    if not entries:
        return
    jobs = [job for _message, job, _heartbeat in entries]
    started_at = time.perf_counter()
    try:
        def commit_fenced():
            with operation_locks(job.scope.operation_id for job in jobs):
                return commit_scope_batch(catalogue, jobs)

        await asyncio.to_thread(commit_fenced)
    except Exception as exc:
        if len(entries) > 1:
            midpoint = len(entries) // 2
            await _commit_materialization_entries(
                jetstream, catalogue, entries[:midpoint]
            )
            await _commit_materialization_entries(
                jetstream, catalogue, entries[midpoint:]
            )
            return
        message, job, heartbeat = entries[0]
        materialization_metrics.operation(
            phase="commit",
            outcome="failed",
            duration_seconds=time.perf_counter() - started_at,
        )
        logging.exception(
            "materialization commit failed for operation %s", job.scope.operation_id
        )
        deliveries = message.metadata.num_delivered
        maximum = get_int("ATLAS_MATERIALIZATION_MAX_DELIVER")
        if deliveries < maximum:
            await message.nak(delay=5)
        else:
            failure = MaterializationFailureJob(
                scope=job.scope,
                error=str(exc),
                started_at=job.started_at,
                completed_at=datetime.now(UTC),
            )
            dead_letter = MaterializationDeadLetter(
                job=job.scope,
                stage="commit",
                error=str(exc),
                delivery_count=deliveries,
                failed_at=datetime.now(UTC),
                staging_key=job.staging_key,
            )
            try:
                await asyncio.to_thread(record_scope_failure, catalogue, failure)
                await jetstream.publish(
                    DEAD_LETTER_SUBJECT,
                    dead_letter.model_dump_json().encode(),
                    headers={
                        "Nats-Msg-Id": f"{job.scope.operation_id}-commit-dead"
                    },
                )
            except Exception:
                logging.exception("failed to persist materialization dead letter")
                await message.nak(delay=30)
            else:
                await message.term()
        await _cancel_task(heartbeat)
        return
    materialization_metrics.operation(
        phase="commit",
        outcome="succeeded",
        duration_seconds=time.perf_counter() - started_at,
    )
    for message, job, heartbeat in entries:
        await _settle_crawl_fanouts(jetstream, catalogue, job.scope)
        await message.ack()
        await _cancel_task(heartbeat)


async def _settle_crawl_fanouts(jetstream, catalogue, scope) -> None:
    store = CrawlMaterializationFanoutStore(catalogue)
    crawl_ids = await asyncio.to_thread(
        store.crawls_for_scope,
        materialization_id=scope.materialization_id,
        definition_revision_id=scope.definition_revision_id,
        scope_kind=scope.scope_kind,
        scope_id=scope.scope_id,
    )
    for crawl_id in crawl_ids:
        await asyncio.to_thread(store.refresh, crawl_id)


async def _commit_fanout_plan(jetstream, catalogue, plan) -> None:
    from repository.catalogue.records import CrawlMaterializationFanoutMember

    members = [
        CrawlMaterializationFanoutMember(
            crawl_id=plan.crawl_id,
            materialization_id=scope.materialization_id,
            definition_revision_id=scope.definition_revision_id,
            scope_kind=scope.scope_kind,
            scope_id=scope.scope_id,
        )
        for scope in plan.scopes
    ]
    def plan_fenced():
        with operation_lock(f"crawl-fanout-{plan.crawl_id}"):
            return CrawlMaterializationFanoutStore(catalogue).plan(
                plan.crawl_id,
                members=members,
            )

    fanout = await asyncio.to_thread(plan_fenced)
    for scope in plan.scopes:
        await jetstream.publish(
            SCOPE_LIVE_SUBJECT,
            scope.model_dump_json().encode(),
            headers={"Nats-Msg-Id": scope.operation_id},
        )


def _dematerialize_requested_materialization(catalogue) -> None:
    with session_scope() as session:
        model = session.scalar(
            select(CatalogueMaterialization)
            .where(
                CatalogueMaterialization.archived_at.is_(None),
                CatalogueMaterialization.dematerialization_requested_at.is_not(None),
            )
            .order_by(CatalogueMaterialization.dematerialization_requested_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if model is None:
            return
        present = any(
            table.table_name == model.name
            for table in catalogue.lake.table.list(schema_name="materialized")
        )
        if present:
            MaterializationStore(catalogue).drop_managed(
                name=model.name,
                expected_uuid=model.ducklake_table_uuid,
                materialization_id=model.id,
            )
        model.archived_at = datetime.now(UTC)
        session.flush()


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
    catalogue_write_lock: asyncio.Lock,
    navigation_store,
) -> None:
    """Commit valid jobs while recursively isolating deterministic poison entries."""

    commit_started = time.perf_counter()
    element_rows = sum(value.element_count for value in prepared)
    staged_bytes = sum(value.staged_bytes for value in prepared)
    try:
        def commit_fenced():
            with operation_locks(job.request_id for job in jobs):
                return ingestor.commit_prepared_batch(
                    prepared,
                    cleanup_on_error=False,
                )

        async with catalogue_write_lock:
            results = await asyncio.to_thread(commit_fenced)
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
                catalogue_write_lock,
                navigation_store,
            )
            await _commit_batch_isolated(
                client,
                results_store,
                ingestor,
                jobs[midpoint:],
                messages[midpoint:],
                prepared[midpoint:],
                catalogue_write_lock,
                navigation_store,
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
            if value.navigation_payload is not None:
                if job.crawl.graph_run_id is None or job.crawl.document_id is None:
                    raise ValueError("graph crawl ingestion requires runtime provenance")
                name = navigation_object_name(
                    job.crawl.graph_run_id,
                    job.crawl.document_id,
                    job.crawl.page_url,
                )
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
            if durable_state.navigation is not None:
                await _publish_navigation_readiness(
                    client, job, durable_state.navigation
                )
            await _notify(client, job, durable_state)
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

        package = None
        if reconciled is not None and job.crawl.graph_run_id is not None:
            try:
                prepared = await asyncio.to_thread(
                    ingestor.prepare_from_raw,
                    crawl=job.crawl,
                )
                try:
                    if prepared.navigation_payload is not None:
                        if job.crawl.document_id is None:
                            raise ValueError(
                                "navigation package requires a document identity"
                            )
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
        if durable_state.navigation is not None:
            try:
                await _publish_navigation_readiness(
                    client, job, durable_state.navigation
                )
            except Exception:
                logging.exception("recovered navigation readiness publication failed")
                await message.nak(delay=1)
                return
        await _notify(client, job, durable_state)
        if durable_state.status == "succeeded":
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


async def _maintenance_active(bucket) -> bool:
    if bucket is None:
        return False
    try:
        await bucket.get("global")
    except (KeyNotFoundError, KeyDeletedError):
        return False
    return True


async def _dependency_probe(client, ingestor, monitor: HealthMonitor) -> None:
    interval = get_float("ATLAS_CATALOG_WORKER_HEALTH_PROBE_INTERVAL_SECONDS")
    timeout = get_float("ATLAS_CATALOG_WORKER_HEALTH_PROBE_TIMEOUT_SECONDS")
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
