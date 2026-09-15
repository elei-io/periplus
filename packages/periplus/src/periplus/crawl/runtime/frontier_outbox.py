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
    store: FrontierStore, jetstream, ingestion: ArchivePublisher, *, batch: int = 64
) -> int:
    if not 1 <= batch <= 64:
        raise ValueError("relay claims 1–64 deliveries")
    deliveries = await asyncio.to_thread(
        store.claim_outbox, batch=batch, lease_seconds=610
    )
    observations = [d for d in deliveries if d.kind == "observation"]
    groups = [[d] for d in deliveries if d.kind != "observation"]
    if observations:
        groups.append(observations)
    for group in groups:
        try:
            async with asyncio.timeout(300):
                if group[0].kind == "observation":
                    from periplus.crawl.acquisition.records import VisitEvidence
                    from periplus.ingestion.captures import from_visit

                    receipts = await ingestion.publish_many(
                        [
                            from_visit(VisitEvidence.model_validate(d.payload))
                            for d in group
                        ]
                    )
                    if len(receipts) != len(group):
                        raise ValueError(
                            "Archive receipt count differs from frozen deliveries"
                        )
                else:
                    receipts = [await publish_delivery(group[0], jetstream, ingestion)]
        except asyncio.CancelledError:
            for delivery in group:
                await asyncio.to_thread(
                    store.release_outbox, delivery, "publisher stopped"
                )
            raise
        except Exception as exc:
            logger.warning("frontier publication failed", exc_info=True)
            for delivery in group:
                await asyncio.to_thread(
                    store.release_outbox, delivery, type(exc).__name__
                )
        else:
            for delivery, receipt in zip(group, receipts, strict=True):
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
