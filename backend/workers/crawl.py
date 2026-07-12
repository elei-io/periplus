"""Atlas crawl hot-path worker."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from nats.errors import TimeoutError as NatsTimeoutError
from prometheus_client import start_http_server
from crawl4ai import AsyncWebCrawler
from ducklake_client import DuckLakeError

from actions.crawl.service import crawl_graph_request
from actions.shared.crawl import browser_config_for_mode
from config import get_bool, get_float, get_int, get_optional, get_str
from db.session import session_scope
from repository import RepositoryPipeline, repository_ingestor_from_env
from runtime.context import GraphExecutionContext
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
    list_graph_runs,
    update_crawl_request,
)
from runtime.graph_runs import expire_graph_run, reconcile_pending_admissions, settle_request
from runtime.graph_progress import bootstrap_run_progress, transition_node_progress


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


async def _process_crawl(message, runs, requests, progress, crawler, repository_pipeline) -> None:
    work = CrawlWork.model_validate_json(message.data)
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
    if previous_status != request.status:
        try:
            await transition_node_progress(progress, request, previous_status=previous_status)
        except Exception:
            pass
    run = await get_graph_run(runs, request.graph_run_id)
    if run is None or run.status in {"failed", "cancelled", "completed", "completed_with_errors"}:
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
    try:
        context = GraphExecutionContext(
            graph_id=run.graph_id,
            graph_run_id=run.id,
            graph_node_id=request.node_id,
            crawl_request_id=request.id,
            effective_policy_snapshot_json=request.effective_policy_snapshot_json,
            source_crawl_id=request.source_crawl_id,
            source_edge_id=request.source_edge_id,
        )
        with session_scope() as session:
            page = await crawl_graph_request(
                session=session,
                url=request.url,
                context=context,
                crawler=crawler,
                repository_pipeline=repository_pipeline,
            )
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

        def await_materializations(current: CrawlRequest) -> CrawlRequest:
            if current.status != "crawling" or current.claim_token != claim_token:
                return current
            return current.model_copy(
                update={
                    "status": "awaiting_materializations",
                    "document_id": page.document_id,
                    "claim_token": None,
                    "claim_expires_at": None,
                    "updated_at": datetime.now(UTC),
                }
            )

        previous_status = request.status
        request = await update_crawl_request(requests, request.id, await_materializations)
        if previous_status != request.status:
            try:
                await transition_node_progress(progress, request, previous_status=previous_status)
            except Exception:
                pass
        await message.ack()
    except Exception as exc:
        if _is_transient_catalogue_failure(exc):
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
            await message.nak(delay=1)
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
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


def _is_transient_catalogue_failure(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, DuckLakeError):
            return True
        message = str(current)
        if "DuckLake sql_dicts failed" in message or "INTERNAL Error" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    worker_id = get_optional("ATLAS_CRAWL_WORKER_ID") or f"{os.uname().nodename}:{os.getpid()}"
    capacity = get_int("ATLAS_CRAWL_WORKER_CONCURRENCY")
    metrics_server = None
    if get_bool("ATLAS_METRICS_ENABLED"):
        metrics_server, _metrics_thread = start_http_server(
            get_int("ATLAS_CRAWL_WORKER_METRICS_PORT"),
            addr=get_str("ATLAS_METRICS_HOST"),
        )

    client = await connect_nats()
    jetstream = client.jetstream()
    runs, requests, workers = await ensure_graph_storage(jetstream)
    progress = await ensure_graph_progress_storage(jetstream)
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
        CRAWL_SUBJECT, durable=CRAWL_CONSUMER, stream=GRAPH_STREAM
    )
    active: set[asyncio.Task] = set()
    started = datetime.now(UTC)

    async def presence() -> None:
        while not stop.is_set():
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
            await workers.put(worker_id.replace(":", "-"), state.model_dump_json().encode())
            await asyncio.sleep(5)

    presence_task = asyncio.create_task(presence())
    try:
        async with AsyncWebCrawler(config=browser_config_for_mode("app")) as crawler:
            async with RepositoryPipeline(repository_ingestor_from_env()) as repository_pipeline:
                while not stop.is_set():
                    active = {task for task in active if not task.done()}
                    available = capacity - len(active)
                    if available <= 0:
                        await asyncio.sleep(0.05)
                        continue
                    fetched = False
                    try:
                        messages = await crawl_subscription.fetch(batch=available, timeout=0.1)
                    except (NatsTimeoutError, asyncio.TimeoutError):
                        messages = []
                    fetched = bool(messages)
                    for message in messages:
                        task = asyncio.create_task(_process_crawl(
                            message, runs, requests, progress, crawler, repository_pipeline
                        ))
                        active.add(task)
                    if not fetched:
                        await asyncio.sleep(0.05)
    finally:
        stop.set()
        presence_task.cancel()
        await asyncio.gather(presence_task, *active, return_exceptions=True)
        await client.drain()
        if metrics_server is not None:
            await asyncio.to_thread(metrics_server.shutdown)
            metrics_server.server_close()


def main() -> None:
    argparse.ArgumentParser(description="Run the Atlas crawl worker.").parse_args()
    asyncio.run(run())


if __name__ == "__main__":
    main()
