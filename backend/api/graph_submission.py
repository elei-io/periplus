"""Thin API adapter for the authoritative graph-run admission path."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy.orm import Session

from api.graph_runtime import ApiGraphRuntime
from control.crawl_graphs.schemas import DEFAULT_GRAPH_RUN_MAX_CRAWLS
from control.crawl_graphs.service import freeze_graph
from runtime.graph_queue import GraphRun
from runtime.edge_sql import FrozenEdgeSql
from runtime.edge_sql import freeze_edge_sql
from repository.catalogue.compiler_definitions import (
    CatalogueCompilerDefinitions,
)
from runtime.graph_runs import (
    create_graph_run,
    resolve_policy_snapshots,
)
from runtime.graph_queue import normalize_request_url


def frozen_edge_compiler(
    definitions: CatalogueCompilerDefinitions,
) -> Callable[[str], Awaitable[FrozenEdgeSql]]:
    async def compile_one(sql: str) -> FrozenEdgeSql:
        return freeze_edge_sql(
            sql,
            purpose=definitions.graph_edge_purpose(),
            catalogue_revision=definitions.revision,
        )

    return compile_one


async def submit_graph_run(
    session: Session,
    *,
    runtime: ApiGraphRuntime,
    graph_id: UUID,
    urls: list[str],
    catalogue_snapshot_resolver: Callable[[], Awaitable[int | None]],
    edge_compiler: Callable[[str], Awaitable[FrozenEdgeSql]] | None = None,
    trigger_kind: str = "manual",
    trigger_schedule_id: UUID | None = None,
    max_crawls: int = DEFAULT_GRAPH_RUN_MAX_CRAWLS,
    max_run_seconds: int | None = None,
) -> GraphRun:
    snapshot = freeze_graph(session, graph_id)
    normalized_urls = list(
        dict.fromkeys(normalize_request_url(url) for url in urls)
    )
    policies = resolve_policy_snapshots(session, normalized_urls)
    session.commit()
    return await create_graph_run(
        runs=runtime.runs,
        requests=runtime.requests,
        progress=runtime.runs,
        jetstream=runtime.jetstream,
        snapshot=snapshot,
        urls=normalized_urls,
        policy_resolver=policies.__getitem__,
        catalogue_snapshot_resolver=catalogue_snapshot_resolver,
        edge_compiler=edge_compiler,
        trigger_kind=trigger_kind,
        trigger_schedule_id=trigger_schedule_id,
        max_crawls=max_crawls,
        max_run_seconds=max_run_seconds,
    )
