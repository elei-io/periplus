"""NATS-backed crawl-graph runtime worker."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
from datetime import UTC, datetime

from nats.errors import TimeoutError as NatsTimeoutError
from prometheus_client import start_http_server

from actions.crawl.service import crawl_graph_request
from config import get_bool, get_int, get_optional, get_str
from db.session import session_scope
from repository.catalogue import catalogue_from_env
from repository.catalogue.query import execute_arrow_query
from runtime.context import GraphExecutionContext
from runtime.graph_queue import (
    CRAWL_CONSUMER,
    CRAWL_SUBJECT,
    EDGE_CONSUMER,
    EDGE_SUBJECT,
    GRAPH_STREAM,
    READINESS_CONSUMER,
    READINESS_SUBJECT,
    CrawlRequest,
    CrawlWork,
    EdgeWork,
    ReadinessWork,
    WorkerState,
    connect_nats,
    ensure_graph_storage,
    ensure_graph_progress_storage,
    get_crawl_request,
    get_graph_run,
    list_graph_runs,
    update_crawl_request,
)
from runtime.graph_runs import (
    evaluate_edge,
    handle_readiness,
    resolve_policy_snapshot,
    settle_request,
)
from runtime.graph_progress import bootstrap_run_progress, transition_node_progress


async def _keep_alive(message) -> None:
    while True:
        await asyncio.sleep(15)
        await message.in_progress()


async def _process_crawl(message, runs, requests, progress) -> None:
    work = CrawlWork.model_validate_json(message.data)
    request = await get_crawl_request(requests, work.crawl_request_id)
    if request is None or request.status in {"completed", "failed", "cancelled"}:
        await message.ack()
        return
    claimed = False
    now = datetime.now(UTC)

    def claim(current: CrawlRequest) -> CrawlRequest:
        nonlocal claimed
        if current.status != "queued":
            return current
        claimed = True
        return current.model_copy(update={"status": "crawling", "updated_at": now})

    request = await update_crawl_request(requests, request.id, claim)
    if not claimed:
        await message.ack()
        return
    await transition_node_progress(progress, request, previous_status="queued")
    run = await get_graph_run(runs, request.graph_run_id)
    if run is None or run.status in {"failed", "cancelled", "completed", "completed_with_errors"}:
        await settle_request(
            runs=runs,
            requests=requests,
            progress=progress,
            request_id=request.id,
            status="cancelled",
            error="Graph run is no longer active.",
        )
        await message.ack()
        return

    heartbeat = asyncio.create_task(_keep_alive(message))
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
            )
        if page.crawl_id != request.id:
            raise RuntimeError(
                f"crawl request {request.id} returned unexpected crawl id {page.crawl_id}"
            )

        def await_materializations(current: CrawlRequest) -> CrawlRequest:
            if current.status != "crawling":
                return current
            return current.model_copy(
                update={
                    "status": "awaiting_materializations",
                    "updated_at": datetime.now(UTC),
                }
            )

        previous_status = request.status
        request = await update_crawl_request(requests, request.id, await_materializations)
        if previous_status != request.status:
            await transition_node_progress(progress, request, previous_status=previous_status)
        await message.ack()
    except Exception as exc:
        await settle_request(
            runs=runs,
            requests=requests,
            progress=progress,
            request_id=request.id,
            status="failed",
            error=str(exc),
        )
        await message.ack()
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


async def _process_readiness(message, runs, requests, progress, jetstream) -> None:
    event = ReadinessWork.model_validate_json(message.data)
    try:
        await handle_readiness(
            runs=runs,
            requests=requests,
            progress=progress,
            jetstream=jetstream,
            event=event,
        )
    except Exception:
        await message.nak(delay=1)
        return
    await message.ack()


def _edge_urls(sql: str, parameters: dict[str, object]):
    with catalogue_from_env() as catalogue:
        reader = execute_arrow_query(catalogue, sql, parameters)
        index = reader.schema.get_field_index("url")
        if index < 0:
            raise ValueError("Edge SQL did not return its required url column.")
        for batch in reader:
            for value in batch.column(index).to_pylist():
                if value is None:
                    continue
                yield str(value)


async def _process_edge(message, runs, requests, progress, jetstream) -> None:
    work = EdgeWork.model_validate_json(message.data)
    try:
        with session_scope() as session:
            await evaluate_edge(
                runs=runs,
                requests=requests,
                progress=progress,
                jetstream=jetstream,
                work=work,
                execute_urls=_edge_urls,
                policy_resolver=lambda url: resolve_policy_snapshot(session, url),
            )
    except Exception:
        # evaluate_edge durably marks deterministic evaluation failures and settles
        # their source request, so redelivery cannot make the query succeed.
        await message.ack()
        return
    await message.ack()


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    worker_id = get_optional("ATLAS_RUNTIME_WORKER_ID") or f"{os.uname().nodename}:{os.getpid()}"
    capacity = get_int("ATLAS_RUNTIME_WORKER_CONCURRENCY")
    metrics_server = None
    if get_bool("ATLAS_METRICS_ENABLED"):
        metrics_server, _metrics_thread = start_http_server(
            get_int("ATLAS_RUNTIME_WORKER_METRICS_PORT"),
            addr=get_str("ATLAS_METRICS_HOST"),
        )

    client = await connect_nats()
    jetstream = client.jetstream()
    runs, requests, workers = await ensure_graph_storage(jetstream)
    progress = await ensure_graph_progress_storage(jetstream)
    for active_run in await list_graph_runs(runs):
        if active_run.status in {"queued", "running"}:
            await bootstrap_run_progress(progress, requests, active_run)
    crawl_subscription = await jetstream.pull_subscribe(
        CRAWL_SUBJECT, durable=CRAWL_CONSUMER, stream=GRAPH_STREAM
    )
    edge_subscription = await jetstream.pull_subscribe(
        EDGE_SUBJECT, durable=EDGE_CONSUMER, stream=GRAPH_STREAM
    )
    readiness_subscription = await jetstream.pull_subscribe(
        READINESS_SUBJECT, durable=READINESS_CONSUMER, stream=GRAPH_STREAM
    )
    subscriptions = (
        (readiness_subscription, _process_readiness),
        (edge_subscription, _process_edge),
        (crawl_subscription, _process_crawl),
    )
    active: set[asyncio.Task] = set()
    started = datetime.now(UTC)

    async def presence() -> None:
        while not stop.is_set():
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
        while not stop.is_set():
            active = {task for task in active if not task.done()}
            available = capacity - len(active)
            if available <= 0:
                await asyncio.sleep(0.05)
                continue
            fetched = False
            for subscription, processor in subscriptions:
                try:
                    messages = await subscription.fetch(batch=available, timeout=0.1)
                except (NatsTimeoutError, asyncio.TimeoutError):
                    continue
                fetched = fetched or bool(messages)
                for message in messages:
                    if processor is _process_crawl:
                        task = asyncio.create_task(processor(message, runs, requests, progress))
                    else:
                        task = asyncio.create_task(processor(message, runs, requests, progress, jetstream))
                    active.add(task)
                    available -= 1
                    if available <= 0:
                        break
                if available <= 0:
                    break
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
    argparse.ArgumentParser(description="Run the Atlas crawl-graph worker.").parse_args()
    asyncio.run(run())


if __name__ == "__main__":
    main()
