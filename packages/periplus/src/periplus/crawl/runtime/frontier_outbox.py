"""Relay committed frontier work using process-owned queue handles."""
import asyncio
import logging

from periplus.crawl.runtime.frontier_store import FrontierDelivery, FrontierStore
from periplus.ingestion.queue import IngestionQueueClient

from periplus.crawl.runtime.frontier_queue import CAPTURE_STREAM, CAPTURE_SUBJECT, CaptureWork
logger = logging.getLogger(__name__)


async def publish_delivery(delivery: FrontierDelivery, jetstream,
                           ingestion: IngestionQueueClient) -> None:
    if delivery.kind == "capture":
        work = CaptureWork.model_validate(delivery.payload)
        if delivery.message_id != work.message_id:
            raise ValueError("capture message identity does not match work")
        await jetstream.publish(
            CAPTURE_SUBJECT, work.model_dump_json().encode(),
            stream=CAPTURE_STREAM, headers={"Nats-Msg-Id": delivery.message_id},
        )
    elif delivery.kind in ("observation", "lineage"):
        await ingestion.enqueue(delivery.ingestion_job())
    else:
        raise ValueError(f"unknown frontier delivery kind: {delivery.kind}")


async def publish_outbox_once(store: FrontierStore, jetstream,
                              ingestion: IngestionQueueClient, *, batch: int = 8) -> int:
    # Serial five-second publishes keep an eight-item batch inside its one-minute
    # claim lease. If a database round trip stalls, expired claims safely replay.
    if not 1 <= batch <= 8:
        raise ValueError("relay batch must be between one and eight")
    deliveries = await asyncio.to_thread(store.claim_outbox, batch=batch, lease_seconds=60)
    for delivery in deliveries:
        try:
            async with asyncio.timeout(5):
                await publish_delivery(delivery, jetstream, ingestion)
        except asyncio.CancelledError:
            # Claims expire even if cancellation also interrupts this best-effort release.
            await asyncio.to_thread(store.release_outbox, delivery, "publisher stopped")
            raise
        except Exception as exc:
            logger.warning("frontier publication failed message=%s", delivery.message_id, exc_info=True)
            await asyncio.to_thread(store.release_outbox, delivery, type(exc).__name__)
        else:
            await asyncio.to_thread(store.mark_outbox_published, delivery)
    return len(deliveries)


async def run_outbox_relay(store: FrontierStore, jetstream,
                           ingestion: IngestionQueueClient, *, stop: asyncio.Event) -> None:
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


async def reconcile_receipts_once(store: FrontierStore, ingestion: IngestionQueueClient, *,
                                  batch: int = 8) -> int:
    deliveries = await asyncio.to_thread(store.claim_ingestion_receipts, batch=batch)
    for delivery in deliveries:
        try:
            async with asyncio.timeout(5):
                state = await ingestion.reconcile(delivery.ingestion_job())
            if state.status == "succeeded":
                await asyncio.to_thread(store.record_ingestion_receipt, delivery, state)
            else:
                await asyncio.to_thread(store.defer_ingestion_receipt, delivery,
                                        "ingestion_pending" if state.status == "pending" else "ingestion_failed")
        except asyncio.CancelledError:
            await asyncio.to_thread(store.defer_ingestion_receipt, delivery, "receipt_checker_stopped")
            raise
        except Exception as exc:
            logger.warning("ingestion receipt deferred message=%s", delivery.message_id, exc_info=True)
            await asyncio.to_thread(store.defer_ingestion_receipt, delivery, type(exc).__name__)
    return len(deliveries)


async def run_ingestion_receipts(store: FrontierStore, ingestion: IngestionQueueClient, *,
                                 stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            worked = await reconcile_receipts_once(store, ingestion)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("frontier ingestion receipts unavailable")
            worked = 0
        if not worked:
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
