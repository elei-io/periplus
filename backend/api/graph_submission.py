"""Thin API adapter for the authoritative graph-run admission path."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy.orm import Session

from control.crawl_graphs.service import freeze_graph
from runtime.graph_queue import (
    GraphRun,
    connect_nats,
    ensure_graph_progress_storage,
    ensure_graph_storage,
)
from runtime.graph_runs import create_graph_run, resolve_policy_snapshot


async def submit_graph_run(
    session: Session,
    *,
    graph_id: UUID,
    urls: list[str],
    catalogue_snapshot_resolver: Callable[[], Awaitable[int | None]],
    trigger_kind: str = "manual",
    trigger_schedule_id: UUID | None = None,
) -> GraphRun:
    snapshot = freeze_graph(session, graph_id)
    session.commit()
    client = await connect_nats()
    try:
        jetstream = client.jetstream()
        runs, requests, _workers = await ensure_graph_storage(jetstream)
        progress = await ensure_graph_progress_storage(jetstream)
        return await create_graph_run(
            runs=runs,
            requests=requests,
            progress=progress,
            jetstream=jetstream,
            snapshot=snapshot,
            urls=urls,
            policy_resolver=lambda url: resolve_policy_snapshot(session, url),
            catalogue_snapshot_resolver=catalogue_snapshot_resolver,
            trigger_kind=trigger_kind,
            trigger_schedule_id=trigger_schedule_id,
        )
    finally:
        await client.drain()
