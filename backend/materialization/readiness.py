"""Crawl readiness notifications derived from authoritative DuckLake state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict

from repository.catalogue.fanout import CrawlMaterializationFanoutStore

READINESS_SUBJECT = "atlas.graph.readiness"
_EVENT_NAMESPACE = UUID("a716ef93-88fd-4d04-9909-e3cde2cd8cb5")


class CrawlReadinessEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID
    crawl_id: UUID
    graph_run_id: UUID
    crawl_request_id: UUID
    status: Literal["ready", "failed"]
    failed_materialization_ids: list[UUID]
    occurred_at: datetime


def readiness_event(catalogue, crawl_id: UUID) -> CrawlReadinessEvent | None:
    fanout = CrawlMaterializationFanoutStore(catalogue).get(crawl_id)
    if fanout is None or fanout.completed_at is None:
        return None
    crawl = catalogue.connection.execute(
        f"SELECT graph_run_id, crawl_request_id FROM {_table(catalogue, 'crawls')} "
        "WHERE crawl_id = ? LIMIT 1",
        [crawl_id],
    ).fetchone()
    if crawl is None:
        raise ValueError(f"crawl {crawl_id} is missing while reconciling readiness")
    failures = catalogue.connection.execute(
        f"SELECT DISTINCT materialization_id FROM "
        f"{_table(catalogue, 'crawl_materialization_fanout_members')} "
        "WHERE crawl_id = ? AND status = 'failed' "
        "ORDER BY materialization_id",
        [crawl_id],
    ).fetchall()
    failed_ids = [UUID(str(row[0])) for row in failures]
    status: Literal["ready", "failed"] = "failed" if failed_ids else "ready"
    event_id = uuid5(_EVENT_NAMESPACE, f"{crawl_id}:{status}:{','.join(map(str, failed_ids))}")
    return CrawlReadinessEvent(
        event_id=event_id,
        crawl_id=crawl_id,
        graph_run_id=UUID(str(crawl[0])),
        crawl_request_id=UUID(str(crawl[1])),
        status=status,
        failed_materialization_ids=failed_ids,
        occurred_at=fanout.completed_at,
    )


async def publish_readiness(jetstream, event: CrawlReadinessEvent) -> None:
    await jetstream.publish(
        READINESS_SUBJECT,
        event.model_dump_json().encode(),
        headers={"Nats-Msg-Id": str(event.event_id)},
    )


async def reconcile_readiness(
    jetstream, catalogue, *, limit: int = 100, offset: int = 0
) -> int:
    """Republish terminal fan-outs; runtime event identity makes this idempotent."""

    published = 0
    for fanout in CrawlMaterializationFanoutStore(catalogue).terminal(
        limit=limit, offset=offset
    ):
        event = readiness_event(catalogue, fanout.crawl_id)
        if event is not None:
            await publish_readiness(jetstream, event)
            published += 1
    return published


async def run_readiness_reconciliation(jetstream, stop, monitor=None) -> None:
    """Recover lost readiness wakeups from authoritative terminal fan-outs."""

    import asyncio

    from repository.catalogue import catalogue_from_env

    while not stop.is_set():
        offset = 0
        with catalogue_from_env() as catalogue:
            if monitor is not None:
                monitor.subsystem_ready("readiness_reconciliation")
            while not stop.is_set():
                count = await reconcile_readiness(
                    jetstream, catalogue, limit=100, offset=offset
                )
                if count < 100:
                    break
                offset += count
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except TimeoutError:
            pass


def _table(catalogue, name: str) -> str:
    return ".".join(
        '"' + part.replace('"', '""') + '"'
        for part in (catalogue.config.alias, catalogue.config.schema, name)
    )
