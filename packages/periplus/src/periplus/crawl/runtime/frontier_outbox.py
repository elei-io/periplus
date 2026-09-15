"""Relay committed frontier work using process-owned queue handles."""

import asyncio
import logging

from periplus.crawl.runtime.frontier_store import FrontierDelivery, FrontierStore
from periplus.ingestion.queue import ArchivePublisher

from periplus.crawl.runtime.frontier_queue import (
    CAPTURE_STREAM,
    CAPTURE_SUBJECT,
    CaptureWork,
)

logger = logging.getLogger(__name__)


async def publish_delivery(
    delivery: FrontierDelivery, jetstream, ingestion: ArchivePublisher
) -> None:
    if delivery.kind == "capture":
        work = CaptureWork.model_validate(delivery.payload)
        if delivery.message_id != work.message_id:
            raise ValueError("capture message identity does not match work")
        await jetstream.publish(
            CAPTURE_SUBJECT,
            work.model_dump_json().encode(),
            stream=CAPTURE_STREAM,
            headers={"Nats-Msg-Id": delivery.message_id},
        )
    elif delivery.kind == "observation":
        from periplus.crawl.acquisition.records import VisitEvidence

        return await ingestion.enqueue_visit(
            VisitEvidence.model_validate(delivery.payload)
        )
    else:
        raise ValueError(f"unknown frontier delivery kind: {delivery.kind}")


async def publish_outbox_once(
    store: FrontierStore, jetstream, ingestion: ArchivePublisher, *, batch: int = 1
) -> int:
    # Claim one bounded archive write; expired claims safely replay.
    if batch != 1:
        raise ValueError("relay claims one write at a time")
    deliveries = await asyncio.to_thread(
        store.claim_outbox, batch=batch, lease_seconds=610
    )
    for delivery in deliveries:
        try:
            async with asyncio.timeout(300):
                receipt = await publish_delivery(delivery, jetstream, ingestion)
        except asyncio.CancelledError:
            # Claims expire even if cancellation also interrupts this best-effort release.
            await asyncio.to_thread(store.release_outbox, delivery, "publisher stopped")
            raise
        except Exception as exc:
            logger.warning(
                "frontier publication failed message=%s",
                delivery.message_id,
                exc_info=True,
            )
            await asyncio.to_thread(store.release_outbox, delivery, type(exc).__name__)
        else:
            await asyncio.to_thread(
                store.mark_outbox_published, delivery, archive_event=receipt
            )
    return len(deliveries)


async def run_outbox_relay(
    store: FrontierStore, jetstream, ingestion: ArchivePublisher, *, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        try:
            worked = await publish_outbox_once(store, jetstream, ingestion)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("frontier outbox unavailable")
            worked = 0
        if not worked:
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
