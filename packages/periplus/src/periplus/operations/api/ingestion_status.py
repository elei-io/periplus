"""Inspect archive coverage and actionable material batches from their owners."""

import asyncio
from datetime import UTC, datetime
from fastapi import APIRouter, Request
from periplus.ingestion.archive import Archive
from periplus.materialization.rebuilds.control import BuildControl
from periplus.materialization.rebuilds.runtime import material_consumer
from nats.js.errors import NotFoundError
from periplus.platform.messaging.catalogue_queue import MATERIAL_STREAM

router = APIRouter(prefix="/operations")


@router.get("/ingestion")
async def ingestion_status(request: Request):
    archive = Archive(request.app.state.document_store)
    control = BuildControl()
    heads = await asyncio.to_thread(archive.heads)

    def coverage():
        items = []
        recipes = set()
        for build in control.builds():
            if not build.protected:
                continue
            recipes.add(build.recipe)
            ranges = control.ranges(build.id)
            lag = sum(
                r.upper - r.cursor + max(0, heads[r.shard] - r.live_cursor)
                for r in ranges
            )
            batches = control.batches(build.id)
            items.append(
                {
                    "id": str(build.id),
                    "phase": build.phase,
                    "source_lag": lag if ranges else None,
                    "failed_batches": sum(b.status == "failed" for b in batches),
                    "running_batches": sum(b.status == "running" for b in batches),
                    "blocker": build.blocker,
                }
            )
        return items, recipes

    targets, recipes = await asyncio.to_thread(coverage)
    queue = dict(pending=0, ack_pending=0, redelivered=0)
    for recipe in recipes:
        try:
            info = await request.app.state.jetstream.consumer_info(
                MATERIAL_STREAM, material_consumer(recipe)
            )
        except NotFoundError:
            continue
        queue["pending"] += info.num_pending
        queue["ack_pending"] += info.num_ack_pending
        queue["redelivered"] += info.num_redelivered
    return {
        "generated_at": datetime.now(UTC),
        "archive_heads": heads,
        "archive_events": sum(heads),
        "targets": targets,
        "queue": queue,
        "status": "attention"
        if any(t["failed_batches"] or t["blocker"] for t in targets)
        else "processing"
        if any(t["source_lag"] for t in targets)
        else "current",
    }
