"""Standard-CDP Atlas acquisition worker runtime."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from nats.errors import TimeoutError as NatsTimeoutError

from actions.crawl.service import RetryableAcquisitionError, crawl_graph_request
from config import get_float, get_int, get_optional
from config.performance import CRAWL_ACQUISITION_LANES, GRAPH_ACK_WAIT_SECONDS
from observability import crawl_metrics
from repository.ingestion.acquisition import AcquisitionPipeline
from repository.ingestion.health import HealthMonitor
from runtime.context import GraphExecutionContext
from runtime.domain_pacing import ensure_domain_pacing_storage
from runtime.graph_queue import (
    CRAWL_CONSUMER,
    CRAWL_SUBJECT,
    GRAPH_STREAM,
    CrawlRequest,
    CrawlWork,
    WorkerState,
    connect_nats,
    ensure_graph_storage,
    ensure_graph_progress_storage,
    get_crawl_request,
    get_graph_run,
    list_crawl_requests,
    list_graph_runs,
    update_crawl_request,
)
from runtime.resource_governor import (
    ResourceCapacityUnavailable,
    ResourcePermitLost,
    ensure_resource_governor_storage,
    object_request,
)
from runtime.graph_runs import expire_graph_run, reconcile_pending_admissions, settle_request
from runtime.graph_progress import bootstrap_run_progress, transition_node_progress
from workers.lifecycle import (
    WorkerEndpointConfig,
    WorkerEndpoints,
    install_signal_handlers,
)


class AcquisitionClaimLost(RuntimeError):
    """The delivery or crawl-request claim was lost before work could settle."""


async def _keep_alive(message, requests, request_id: UUID, claim_token: UUID) -> None:
    interval = max(1.0, GRAPH_ACK_WAIT_SECONDS / 3)
    while True:
        await asyncio.sleep(interval)
        await message.in_progress()
        expires_at = datetime.now(UTC) + timedelta(
            seconds=GRAPH_ACK_WAIT_SECONDS * 2
        )

        def refresh(current: CrawlRequest) -> CrawlRequest:
            if current.status != "crawling" or current.claim_token != claim_token:
                return current
            return current.model_copy(update={"claim_expires_at": expires_at})

        current = await update_crawl_request(requests, request_id, refresh)
        if current.claim_token != claim_token:
            return


async def _process_crawl(
    message,
    runs,
    requests,
    progress,
    repository_pipeline,
    resource_grants=None,
    domain_pacing=None,
    jetstream=None,
) -> None:
    try:
        work = CrawlWork.model_validate_json(message.data)
    except Exception:
        logging.exception("discarding invalid acquisition work")
        await message.term()
        return
    request = await get_crawl_request(requests, work.crawl_request_id)
    if request is None or request.status in {"completed", "failed", "cancelled"}:
        await message.ack()
        return
    claimed = False
    now = datetime.now(UTC)
    claim_token = uuid4()
    previous_status: str | None = None

    def claim(current: CrawlRequest) -> CrawlRequest:
        nonlocal claimed, previous_status
        previous_status = current.status
        reclaimable = (
            current.status == "crawling"
            and current.claim_expires_at is not None
            and current.claim_expires_at <= now
        )
        if current.status != "queued" and not reclaimable:
            return current
        claimed = True
        return current.model_copy(update={
            "status": "crawling",
            "claim_token": claim_token,
            "claim_expires_at": now + timedelta(
                seconds=GRAPH_ACK_WAIT_SECONDS * 2
            ),
            "updated_at": now,
        })

    request = await update_crawl_request(requests, request.id, claim)
    if not claimed:
        if request.status == "crawling":
            await message.nak(delay=1)
        else:
            await message.ack()
        return
    if previous_status != request.status:
        try:
            await transition_node_progress(progress, request, previous_status=previous_status)
        except Exception:
            pass
    run = await get_graph_run(runs, request.graph_run_id)
    if run is None:
        await settle_request(
                runs=runs,
                requests=requests,
                progress=progress,
                request_id=request.id,
                status="cancelled",
                error="Graph run is no longer active.",
                expected_claim_token=claim_token,
        )
        await message.ack()
        return
    if run.status in {"failed", "cancelled", "completed", "completed_with_errors"}:
        await settle_request(
            runs=runs,
            requests=requests,
            progress=progress,
            request_id=request.id,
            status="cancelled",
            error="Graph run is no longer active.",
            expected_claim_token=claim_token,
        )
        await message.ack()
        return

    heartbeat = asyncio.create_task(
        _keep_alive(message, requests, request.id, claim_token)
    )
    acquisition = None
    max_deliver = get_int("ATLAS_CRAWL_MAX_DELIVER")
    try:
        context = GraphExecutionContext(
            graph_id=run.graph_id,
            graph_run_id=run.id,
            graph_node_id=request.node_id,
            crawl_request_id=request.id,
            effective_policy_snapshot_json=request.effective_policy_snapshot_json,
            prior_attempts_json=request.acquisition_attempts_json,
            source_crawl_id=request.source_crawl_id,
            source_edge_id=request.source_edge_id,
        )
        acquisition = asyncio.create_task(
            crawl_graph_request(
                session=None,
                url=request.url,
                context=context,
                resource_grants=resource_grants,
                domain_pacing=domain_pacing,
                repository_pipeline=repository_pipeline,
                persist_retryable_failure=(
                    request.processing_failure_count + 1 >= max_deliver
                ),
            )
        )
        done, _pending = await asyncio.wait(
            (acquisition, heartbeat), return_when=asyncio.FIRST_COMPLETED
        )
        if heartbeat in done:
            acquisition.cancel()
            await asyncio.gather(acquisition, return_exceptions=True)
            if heartbeat.cancelled():
                raise AcquisitionClaimLost("acquisition claim heartbeat was cancelled")
            heartbeat_error = heartbeat.exception()
            if heartbeat_error is not None:
                raise AcquisitionClaimLost("acquisition claim heartbeat failed") from heartbeat_error
            raise AcquisitionClaimLost("acquisition claim ownership was lost")
        page = await acquisition
        if page.crawl_id != request.id:
            raise RuntimeError(
                f"crawl request {request.id} returned unexpected crawl id {page.crawl_id}"
            )
        if not page.success:
            await settle_request(
                    runs=runs,
                    requests=requests,
                    progress=progress,
                    request_id=request.id,
                    status="failed",
                    error=page.error or "Page acquisition failed.",
                    expected_claim_token=claim_token,
                    failure_stage="acquisition",
            )
            await message.ack()
            return

        def await_navigation(current: CrawlRequest) -> CrawlRequest:
            if current.status != "crawling" or current.claim_token != claim_token:
                return current
            return current.model_copy(
                update={
                    "status": "awaiting_navigation",
                    "document_id": page.document_id,
                    "claim_token": None,
                    "claim_expires_at": None,
                    "updated_at": datetime.now(UTC),
                }
            )

        previous_status = request.status
        request = await update_crawl_request(requests, request.id, await_navigation)
        if previous_status != request.status:
            try:
                await transition_node_progress(progress, request, previous_status=previous_status)
            except Exception:
                pass
        await message.ack()
    except Exception as exc:
        infrastructure_failure = isinstance(
            exc,
            (
                AcquisitionClaimLost,
                NatsTimeoutError,
                OSError,
                ResourceCapacityUnavailable,
                ResourcePermitLost,
            ),
        )
        failure_count = request.processing_failure_count
        logging.exception(
            "crawl acquisition attempt failed",
            extra={
                "crawl_request_id": str(request.id),
                "graph_run_id": str(request.graph_run_id),
                "url": request.url,
                "infrastructure_failure": infrastructure_failure,
                "processing_failure_count": failure_count,
            },
        )
        if not infrastructure_failure:
            def record_failure(current: CrawlRequest) -> CrawlRequest:
                if current.status != "crawling" or current.claim_token != claim_token:
                    return current
                attempts = current.acquisition_attempts_json
                if isinstance(exc, RetryableAcquisitionError) and exc.page.attempt_evidence is not None:
                    attempts = (*attempts, exc.page.attempt_evidence.model_dump(mode="json"))
                return current.model_copy(update={
                    "processing_failure_count": current.processing_failure_count + 1,
                    "acquisition_attempts_json": attempts,
                })

            request = await update_crawl_request(requests, request.id, record_failure)
            failure_count = request.processing_failure_count

        if infrastructure_failure or failure_count < max_deliver:
            def release_claim(current: CrawlRequest) -> CrawlRequest:
                if current.status != "crawling" or current.claim_token != claim_token:
                    return current
                return current.model_copy(
                    update={
                        "status": "queued",
                        "claim_token": None,
                        "claim_expires_at": None,
                        "updated_at": datetime.now(UTC),
                    }
                )

            current = await update_crawl_request(requests, request.id, release_claim)
            if current.status == "queued":
                try:
                    await transition_node_progress(
                        progress, current, previous_status="crawling"
                    )
                except Exception:
                    pass
            retry_after = (
                exc.retry_after_seconds
                if isinstance(exc, RetryableAcquisitionError)
                else None
            )
            delay = (
                retry_after
                if retry_after is not None
                else min(30, 2 ** max(0, failure_count - 1))
            )
            await message.nak(delay=delay)
            return
        await settle_request(
                runs=runs,
                requests=requests,
                progress=progress,
                request_id=request.id,
                status="failed",
                error=str(exc),
                expected_claim_token=claim_token,
                failure_stage="acquisition",
        )
        await message.ack()
    finally:
        if acquisition is not None and not acquisition.done():
            acquisition.cancel()
            await asyncio.gather(acquisition, return_exceptions=True)
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)

async def run() -> None:
    stop = asyncio.Event()
    install_signal_handlers(stop)

    worker_id = get_optional("ATLAS_ACQUISITION_WORKER_ID") or (
        f"acquisition:{os.uname().nodename}:{os.getpid()}"
    )
    capacity = CRAWL_ACQUISITION_LANES
    object_request(
        "acquisition-startup-validation",
        direction="write",
        byte_count=get_int("ATLAS_REPOSITORY_MAX_HTML_BYTES"),
        service_class="critical",
    )
    endpoints = WorkerEndpoints(WorkerEndpointConfig.from_env("acquisition"))
    endpoints.start_metrics()

    client = await connect_nats()
    jetstream = client.jetstream()
    runs, requests, workers = await ensure_graph_storage(jetstream)
    progress = await ensure_graph_progress_storage(jetstream)
    resource_grants = await ensure_resource_governor_storage(jetstream)
    domain_pacing = await ensure_domain_pacing_storage(jetstream)
    for active_run in await list_graph_runs(runs):
        if active_run.status in {"queued", "running"}:
            await bootstrap_run_progress(progress, requests, active_run)
            await reconcile_pending_admissions(
                runs=runs,
                requests=requests,
                progress=progress,
                jetstream=jetstream,
                run=active_run,
            )
    crawl_subscription = await jetstream.pull_subscribe(
        CRAWL_SUBJECT,
        durable=CRAWL_CONSUMER,
        stream=GRAPH_STREAM,
    )
    active: set[asyncio.Task] = set()
    started = datetime.now(UTC)
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_ACQUISITION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    monitor.dependencies_ready()
    monitor.subsystem_ready("acquisition")
    endpoints.start_health(monitor)

    async def presence() -> None:
        while not stop.is_set():
            monitor.heartbeat()
            for active_run in await list_graph_runs(runs):
                if active_run.status in {"queued", "running"}:
                    active_run = await expire_graph_run(
                        runs=runs,
                        requests=requests,
                        progress=progress,
                        run=active_run,
                    )
                    if active_run.status in {"queued", "running"} and active_run.pending_admissions:
                        await reconcile_pending_admissions(
                            runs=runs,
                            requests=requests,
                            progress=progress,
                            jetstream=jetstream,
                            run=active_run,
                        )
            state = WorkerState(
                worker_id=worker_id,
                started_at=started,
                last_seen_at=datetime.now(UTC),
                capacity=capacity,
                active_request_count=len(active),
                stopping=False,
            )
            await workers.put(
                worker_id.replace(":", "-"), state.model_dump_json().encode()
            )
            all_requests = await list_crawl_requests(requests)
            pending_requests = [
                item
                for item in all_requests
                if item.status in {"queued", "crawling"}
            ]
            now = datetime.now(UTC)
            oldest_age = (
                max(
                    0.0,
                    (
                        now
                        - min(item.created_at for item in pending_requests)
                    ).total_seconds(),
                )
                if pending_requests
                else 0.0
            )
            completed_acquisitions = [
                item
                for item in all_requests
                if item.status not in {"queued", "crawling"}
            ]
            marker = (
                len(completed_acquisitions),
                max(
                    (item.updated_at for item in completed_acquisitions),
                    default=started,
                ).isoformat(),
            )
            monitor.queue_observed(
                "crawl_acquisition",
                pending=len(pending_requests),
                progress_marker=marker,
                stalled_after_seconds=get_float("ATLAS_WORKER_QUEUE_STALL_SECONDS"),
            )
            crawl_metrics.queue_state(
                pending=len(pending_requests),
                oldest_age_seconds=oldest_age,
            )
            await asyncio.sleep(5)

    presence_task = asyncio.create_task(presence())
    try:
        async with AcquisitionPipeline() as repository_pipeline:
            while not stop.is_set():
                completed = {task for task in active if task.done()}
                for task in completed:
                    if task.cancelled():
                        continue
                    error = task.exception()
                    if error is not None:
                        logging.error(
                            "acquisition task exited unexpectedly",
                            exc_info=(type(error), error, error.__traceback__),
                        )
                active.difference_update(completed)
                available = capacity - len(active)
                if available <= 0:
                    await asyncio.sleep(0.05)
                    continue
                try:
                    messages = await crawl_subscription.fetch(batch=available, timeout=0.1)
                except (NatsTimeoutError, asyncio.TimeoutError):
                    messages = []
                for message in messages:
                    task = asyncio.create_task(
                        _process_crawl(
                            message, runs, requests, progress, repository_pipeline,
                            resource_grants, domain_pacing, jetstream,
                        )
                    )
                    active.add(task)
                if not messages:
                    await asyncio.sleep(0.05)
    finally:
        stop.set()
        presence_task.cancel()
        await asyncio.gather(presence_task, *active, return_exceptions=True)
        await client.drain()
        await endpoints.close()
