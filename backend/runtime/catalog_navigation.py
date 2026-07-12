"""Catalog-worker readiness and SQL-edge execution."""

from __future__ import annotations

import asyncio

from nats.errors import TimeoutError as NatsTimeoutError

from config import get_int
from db.session import session_scope
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
)
from runtime.graph_runs import evaluate_edge, handle_readiness, resolve_policy_snapshot


def _edge_urls(sql: str, parameters: dict[str, object]):
    with catalogue_from_env() as catalogue:
        reader = execute_arrow_query(catalogue, sql, parameters)
        index = reader.schema.get_field_index("url")
        if index < 0:
            raise ValueError("Edge SQL did not return its required url column.")
        for batch in reader:
            for value in batch.column(index).to_pylist():
                if value is not None:
                    yield str(value)


async def _process_readiness(message, runs, requests, progress, jetstream) -> None:
    event = ReadinessWork.model_validate_json(message.data)
    try:
        await handle_readiness(
            runs=runs, requests=requests, progress=progress, jetstream=jetstream, event=event
        )
    except Exception:
        await message.nak(delay=1)
        return
    await message.ack()


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
    finally:
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
