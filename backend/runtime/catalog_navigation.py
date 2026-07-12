"""Catalog-worker readiness and SQL-edge execution."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import uuid4

from nats.errors import TimeoutError as NatsTimeoutError

from config import get_float, get_int, get_str
from db.session import session_scope
from materialization.readiness import readiness_event
from repository.catalogue import catalogue_from_env
from repository.catalogue.query import execute_arrow_query
from runtime.graph_queue import (
    EDGE_CONSUMER,
    EDGE_SUBJECT,
    GRAPH_STREAM,
    READINESS_CONSUMER,
    READINESS_SUBJECT,
    EdgeWork,
    ReadinessWork,
    connect_nats,
    ensure_graph_progress_storage,
    ensure_graph_storage,
    edge_evaluation_identity,
    update_edge_evaluation,
)
from runtime.graph_runs import EdgeEvaluationBusy, EdgeEvaluationFailed, evaluate_edge, handle_readiness, resolve_policy_snapshot


class EdgeUrlExecutor:
    """Execute one bounded DuckDB edge query and make it interruptible."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._connection = None

    def interrupt(self) -> None:
        with self._lock:
            connection = self._connection
        if connection is not None:
            connection.interrupt()

    def __call__(self, sql: str, parameters: dict[str, object]) -> list[str]:
        with catalogue_from_env() as catalogue:
            with self._lock:
                self._connection = catalogue.connection
            try:
                catalogue.connection.execute(
                    "SET memory_limit = ?", [get_str("ATLAS_EDGE_QUERY_MEMORY_LIMIT")]
                )
                reader = execute_arrow_query(catalogue, sql, parameters)
                index = reader.schema.get_field_index("url")
                if index < 0:
                    raise ValueError("Edge SQL did not return its required url column.")
                rows = 0
                output_bytes = 0
                urls: list[str] = []
                for batch in reader:
                    rows += batch.num_rows
                    output_bytes += batch.nbytes
                    if rows > get_int("ATLAS_EDGE_MAX_OUTPUT_ROWS"):
                        raise ValueError("Edge SQL exceeded its row limit.")
                    if output_bytes > get_int("ATLAS_EDGE_MAX_OUTPUT_BYTES"):
                        raise ValueError("Edge SQL exceeded its output-byte limit.")
                    urls.extend(
                        str(value)
                        for value in batch.column(index).to_pylist()
                        if value is not None
                    )
                return urls
            finally:
                with self._lock:
                    self._connection = None


async def _process_readiness(message, runs, requests, progress, jetstream) -> None:
    wakeup = ReadinessWork.model_validate_json(message.data)
    try:
        def load_authoritative_readiness():
            with catalogue_from_env() as catalogue:
                return readiness_event(catalogue, wakeup.crawl_id)

        durable = await asyncio.to_thread(load_authoritative_readiness)
        if durable is None:
            await message.nak(delay=1)
            return
        event = ReadinessWork.model_validate(durable.model_dump())
        await handle_readiness(
            runs=runs, requests=requests, progress=progress, jetstream=jetstream, event=event
        )
    except Exception:
        await message.nak(delay=1)
        return
    await message.ack()


async def _process_edge(message, runs, requests, progress, jetstream) -> None:
    work = EdgeWork.model_validate_json(message.data)
    claim_token = uuid4()
    identity = edge_evaluation_identity(
        work.graph_run_id, work.crawl_request_id, work.crawl_id, work.edge_id
    )

    async def keep_alive() -> None:
        interval = max(1.0, get_float("ATLAS_GRAPH_ACK_WAIT_SECONDS") / 3)
        while True:
            await asyncio.sleep(interval)
            await message.in_progress()
            expires_at = datetime.now(UTC) + timedelta(
                seconds=get_float("ATLAS_GRAPH_ACK_WAIT_SECONDS") * 2
            )
            try:
                current = await update_edge_evaluation(
                    requests,
                    identity,
                    lambda value: value if value.claim_token != claim_token else value.model_copy(update={"claim_expires_at": expires_at}),
                )
            except KeyError:
                continue
            if current.claim_token not in {None, claim_token}:
                return

    heartbeat = asyncio.create_task(keep_alive())
    try:
        with session_scope() as session:
            await evaluate_edge(
                runs=runs,
                requests=requests,
                progress=progress,
                jetstream=jetstream,
                work=work,
                execute_urls=EdgeUrlExecutor(),
                policy_resolver=lambda url: resolve_policy_snapshot(session, url),
                claim_token=claim_token,
            )
    except EdgeEvaluationBusy:
        await message.nak(delay=1)
        return
    except EdgeEvaluationFailed:
        await message.ack()
        return
    except Exception:
        await message.nak(delay=1)
        return
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
    # Evaluation records terminal semantic failures itself; redelivery cannot repair SQL.
    await message.ack()


async def run() -> None:
    stop = asyncio.Event()
    client = await connect_nats()
    jetstream = client.jetstream()
    runs, requests, _workers = await ensure_graph_storage(jetstream)
    progress = await ensure_graph_progress_storage(jetstream)
    readiness = await jetstream.pull_subscribe(
        READINESS_SUBJECT, durable=READINESS_CONSUMER, stream=GRAPH_STREAM
    )
    edges = await jetstream.pull_subscribe(
        EDGE_SUBJECT, durable=EDGE_CONSUMER, stream=GRAPH_STREAM
    )
    capacity = get_int("ATLAS_CATALOG_WORKER_CONCURRENCY")
    active: set[asyncio.Task] = set()
    try:
        while not stop.is_set():
            active = {task for task in active if not task.done()}
            available = capacity - len(active)
            if available <= 0:
                await asyncio.sleep(0.05)
                continue
            fetched = False
            for subscription, processor in ((readiness, _process_readiness), (edges, _process_edge)):
                if available <= 0:
                    break
                try:
                    messages = await subscription.fetch(batch=available, timeout=0.1)
                except (NatsTimeoutError, asyncio.TimeoutError):
                    continue
                fetched = fetched or bool(messages)
                for message in messages:
                    active.add(asyncio.create_task(
                        processor(message, runs, requests, progress, jetstream)
                    ))
                    available -= 1
                    if available <= 0:
                        break
            if not fetched:
                await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.gather(*active, return_exceptions=True)
        await client.drain()
