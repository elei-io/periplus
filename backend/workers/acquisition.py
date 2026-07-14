"""Transport-specific Atlas acquisition worker runtime."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from nats.errors import TimeoutError as NatsTimeoutError
from prometheus_client import start_http_server
import httpx

from actions.crawl.service import crawl_graph_request
from config import get_bool, get_float, get_int, get_optional, get_str
from observability import crawl_metrics
from repository.ingestion.acquisition import AcquisitionPipeline
from repository.ingestion.health import HealthMonitor, start_health_server
from runtime.context import GraphExecutionContext
from runtime.graph_queue import (
    CRAWL_CONSUMERS,
    CRAWL_SUBJECTS,
    GRAPH_STREAM,
    CrawlTransport,
    CrawlRequest,
    CrawlWork,
    WorkerState,
    connect_nats,
    ensure_graph_storage,
    ensure_graph_progress_storage,
    ensure_policy_trial_budget_storage,
    get_crawl_request,
    get_graph_run,
    list_crawl_requests,
    list_graph_runs,
    reconcile_policy_trial_budget,
    settle_sample_request,
    update_crawl_request,
)
from runtime.resource_governor import ensure_resource_governor_storage, object_request
from runtime.graph_runs import expire_graph_run, reconcile_pending_admissions, settle_request
from runtime.graph_progress import bootstrap_run_progress, transition_node_progress


class BrowserCrawler(Protocol):
    async def arun(self, *args, **kwargs): ...


async def _keep_alive(message, requests, request_id: UUID, claim_token: UUID) -> None:
    interval = max(1.0, get_float("ATLAS_GRAPH_ACK_WAIT_SECONDS") / 3)
    while True:
        await asyncio.sleep(interval)
        await message.in_progress()
        expires_at = datetime.now(UTC) + timedelta(
            seconds=get_float("ATLAS_GRAPH_ACK_WAIT_SECONDS") * 2
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
    crawler,
    repository_pipeline,
    transport: CrawlTransport,
    resource_grants=None,
    http_client: httpx.AsyncClient | None = None,
    jetstream=None,
) -> None:
    try:
        work = CrawlWork.model_validate_json(message.data)
    except Exception:
        logging.exception("discarding invalid acquisition work")
        await message.term()
        return
    if work.transport != transport:
        await message.term()
        logging.error(
            f"{transport} worker received {work.transport} acquisition work"
        )
        return
    request = await get_crawl_request(requests, work.crawl_request_id)
    if request is None or request.status in {"completed", "failed", "cancelled"}:
        await message.ack()
        return
    if request.transport != transport:
        await message.term()
        logging.error(
            f"crawl request {request.id} is frozen for {request.transport}, not {transport}"
        )
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
                seconds=get_float("ATLAS_GRAPH_ACK_WAIT_SECONDS") * 2
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
    if request.purpose == "use" and previous_status != request.status:
        try:
            await transition_node_progress(progress, request, previous_status=previous_status)
        except Exception:
            pass
    run = await get_graph_run(runs, request.graph_run_id)
    if run is None:
        if request.purpose == "sample":
            await settle_sample_request(
                jetstream,
                requests,
                request.id,
                status="failed",
                error="Originating graph run is unavailable.",
                failure_stage="lifecycle",
                expected_claim_token=claim_token,
            )
        else:
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
    if request.purpose == "use" and run.status in {"failed", "cancelled", "completed", "completed_with_errors"}:
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
    try:
        context = GraphExecutionContext(
            graph_id=run.graph_id,
            graph_run_id=run.id,
            graph_node_id=request.node_id,
            crawl_request_id=request.id,
            effective_policy_snapshot_json=request.effective_policy_snapshot_json,
            purpose=request.purpose,
            trial=(request.trial.model_dump(mode="json") if request.trial else None),
            source_crawl_id=request.source_crawl_id,
            source_edge_id=request.source_edge_id,
        )
        acquisition = asyncio.create_task(
            crawl_graph_request(
                session=None,
                url=request.url,
                context=context,
                crawler=crawler,
                http_client=http_client,
                resource_grants=resource_grants,
                repository_pipeline=repository_pipeline,
            )
        )
        done, _pending = await asyncio.wait(
            (acquisition, heartbeat), return_when=asyncio.FIRST_COMPLETED
        )
        if heartbeat in done:
            acquisition.cancel()
            await asyncio.gather(acquisition, return_exceptions=True)
            if heartbeat.cancelled():
                raise RuntimeError("acquisition claim heartbeat was cancelled")
            heartbeat_error = heartbeat.exception()
            if heartbeat_error is not None:
                raise RuntimeError("acquisition claim heartbeat failed") from heartbeat_error
            raise RuntimeError("acquisition claim ownership was lost")
        page = await acquisition
        if page.crawl_id != request.id:
            raise RuntimeError(
                f"crawl request {request.id} returned unexpected crawl id {page.crawl_id}"
            )
        if not page.success:
            if request.purpose == "sample":
                await settle_sample_request(
                    jetstream,
                    requests,
                    request.id,
                    status="failed",
                    error=page.error or "Page acquisition failed.",
                    expected_claim_token=claim_token,
                    failure_stage="acquisition",
                )
            else:
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
                    "status": (
                        "awaiting_ingestion"
                        if current.purpose == "sample"
                        else "awaiting_navigation"
                    ),
                    "document_id": page.document_id,
                    "claim_token": None,
                    "claim_expires_at": None,
                    "updated_at": datetime.now(UTC),
                }
            )

        previous_status = request.status
        request = await update_crawl_request(requests, request.id, await_navigation)
        if request.purpose == "use" and previous_status != request.status:
            try:
                await transition_node_progress(progress, request, previous_status=previous_status)
            except Exception:
                pass
        await message.ack()
    except Exception as exc:
        delivery_count = int(
            getattr(getattr(message, "metadata", None), "num_delivered", 1)
        )
        if delivery_count < get_int("ATLAS_CRAWL_MAX_DELIVER"):
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
            if request.purpose == "use" and current.status == "queued":
                try:
                    await transition_node_progress(
                        progress, current, previous_status="crawling"
                    )
                except Exception:
                    pass
            await message.nak(delay=min(30, 2 ** max(0, delivery_count - 1)))
            return
        if request.purpose == "sample":
            await settle_sample_request(
                jetstream,
                requests,
                request.id,
                status="failed",
                error=str(exc),
                expected_claim_token=claim_token,
                failure_stage="acquisition",
            )
        else:
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

def _worker_setting(transport: CrawlTransport, suffix: str) -> str:
    prefix = {
        "http": "ATLAS_CRAWL_HTTP_WORKER",
        "browser": "ATLAS_CRAWL_BROWSER_WORKER",
        "firecrawl": "ATLAS_CRAWL_PROVIDER_WORKER",
    }[transport]
    return f"{prefix}_{suffix}"


async def run(
    transport: CrawlTransport,
    *,
    crawler: BrowserCrawler | None = None,
) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for received_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(received_signal, stop.set)

    if transport == "browser" and crawler is None:
        raise ValueError("browser acquisition requires a process-owned crawler")
    if transport != "browser" and crawler is not None:
        raise ValueError(f"{transport} acquisition cannot own a browser crawler")
    worker_id = get_optional(_worker_setting(transport, "ID")) or (
        f"{transport}:{os.uname().nodename}:{os.getpid()}"
    )
    capacity = get_int(_worker_setting(transport, "CONCURRENCY"))
    object_request(
        "acquisition-startup-validation",
        direction="write",
        byte_count=get_int("ATLAS_REPOSITORY_MAX_HTML_BYTES"),
        service_class="critical",
    )
    metrics_server = None
    if get_bool("ATLAS_METRICS_ENABLED"):
        metrics_server, _metrics_thread = start_http_server(
            get_int(_worker_setting(transport, "METRICS_PORT")),
            addr=get_str("ATLAS_METRICS_HOST"),
        )

    client = await connect_nats()
    jetstream = client.jetstream()
    runs, requests, workers = await ensure_graph_storage(jetstream)
    progress = await ensure_graph_progress_storage(jetstream)
    resource_grants = await ensure_resource_governor_storage(jetstream)
    trial_budget = await ensure_policy_trial_budget_storage(jetstream)
    await reconcile_policy_trial_budget(trial_budget, runs, requests)
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
        CRAWL_SUBJECTS[transport],
        durable=CRAWL_CONSUMERS[transport],
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
    health_server, _health_thread = start_health_server(
        address=get_str("ATLAS_ACQUISITION_WORKER_HEALTH_HOST"),
        port=get_int("ATLAS_ACQUISITION_WORKER_HEALTH_PORT"),
        monitor=monitor,
    )

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
                transport=transport,
                started_at=started,
                last_seen_at=datetime.now(UTC),
                capacity=capacity,
                active_request_count=len(active),
                stopping=False,
            )
            await workers.put(
                worker_id.replace(":", "-"), state.model_dump_json().encode()
            )
            all_transport_requests = [
                item
                for item in await list_crawl_requests(requests)
                if item.transport == transport
            ]
            requests_for_transport = [
                item
                for item in all_transport_requests
                if item.status in {"queued", "crawling"}
            ]
            now = datetime.now(UTC)
            oldest_age = (
                max(
                    0.0,
                    (
                        now
                        - min(item.created_at for item in requests_for_transport)
                    ).total_seconds(),
                )
                if requests_for_transport
                else 0.0
            )
            completed_acquisitions = [
                item
                for item in all_transport_requests
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
                f"{transport}_acquisition",
                pending=len(requests_for_transport),
                progress_marker=marker,
                stalled_after_seconds=get_float("ATLAS_WORKER_QUEUE_STALL_SECONDS"),
            )
            crawl_metrics.queue_state(
                transport=transport,
                pending=len(requests_for_transport),
                oldest_age_seconds=oldest_age,
            )
            await asyncio.sleep(5)

    presence_task = asyncio.create_task(presence())
    try:
        async with httpx.AsyncClient() as http_client:
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
                    fetched = False
                    try:
                        messages = await crawl_subscription.fetch(
                            batch=available, timeout=0.1
                        )
                    except (NatsTimeoutError, asyncio.TimeoutError):
                        messages = []
                    fetched = bool(messages)
                    for message in messages:
                        task = asyncio.create_task(
                            _process_crawl(
                                message,
                                runs,
                                requests,
                                progress,
                                crawler,
                                repository_pipeline,
                                transport,
                                resource_grants,
                                http_client,
                                jetstream,
                            )
                        )
                        active.add(task)
                    if not fetched:
                        await asyncio.sleep(0.05)
    finally:
        stop.set()
        presence_task.cancel()
        await asyncio.gather(presence_task, *active, return_exceptions=True)
        await client.drain()
        await asyncio.to_thread(health_server.shutdown)
        health_server.server_close()
        if metrics_server is not None:
            await asyncio.to_thread(metrics_server.shutdown)
            metrics_server.server_close()
