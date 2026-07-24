"""Publish committed graph work without holding a Postgres transaction."""

from __future__ import annotations

import asyncio
import json
import logging

from runtime.graph_queue import GRAPH_STREAM
from runtime.graph_store import AsyncGraphRuntimeStore, OutboxDelivery


async def publish_outbox_once(
    store: AsyncGraphRuntimeStore,
    jetstream,
    *,
    batch: int = 100,
) -> int:
    deliveries = await store.claim_outbox(batch=batch)
    for delivery in deliveries:
        try:
            await _publish(jetstream, delivery)
        except asyncio.CancelledError:
            await store.release_outbox(
                delivery, RuntimeError("publisher stopped")
            )
            raise
        except Exception as exc:
            logging.warning(
                "graph outbox publication failed for %s",
                delivery.message_id,
                exc_info=True,
            )
            await store.release_outbox(delivery, exc)
            continue
        await store.mark_outbox_published(delivery)
    return len(deliveries)


async def run_outbox_relay(
    store: AsyncGraphRuntimeStore,
    jetstream,
    *,
    stop: asyncio.Event,
    idle_interval_seconds: float = 1.0,
) -> None:
    while not stop.is_set():
        worked = await publish_outbox_once(store, jetstream)
        if worked:
            continue
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=idle_interval_seconds
            )
        except TimeoutError:
            pass


async def _publish(jetstream, delivery: OutboxDelivery) -> None:
    await jetstream.publish(
        delivery.subject,
        json.dumps(
            delivery.payload, separators=(",", ":"), sort_keys=True
        ).encode(),
        stream=GRAPH_STREAM,
        headers={"Nats-Msg-Id": delivery.message_id},
    )
