"""Publication ordering and typed ingestion routing for the frontier relay."""
from datetime import UTC, datetime
import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from periplus.crawl.runtime.frontier_outbox import publish_delivery, publish_outbox_once
from periplus.crawl.runtime.frontier_store import FrontierDelivery
from periplus.platform.catalogue.lineage import CollectionDefinition


class FrontierOutboxTests(unittest.IsolatedAsyncioTestCase):
    def delivery(self, kind="capture", payload=None):
        identity = uuid4()
        return FrontierDelivery(f"capture:{identity}:1", identity, kind,
                                payload or {"acquisition_id": str(identity), "generation": 1}, uuid4())

    async def test_published_marker_follows_puback(self):
        delivery = self.delivery()
        calls = []
        store = MagicMock()
        store.claim_outbox.return_value = [delivery]
        store.mark_outbox_published.side_effect = lambda _: calls.append("marked")
        jetstream = AsyncMock()
        jetstream.publish.side_effect = lambda *args, **kwargs: calls.append("published")
        await publish_outbox_once(store, jetstream, AsyncMock())
        self.assertEqual(calls, ["published", "marked"])
        self.assertEqual(jetstream.publish.await_args.kwargs["headers"], {"Nats-Msg-Id": delivery.message_id})
        store.release_outbox.assert_not_called()

    async def test_failed_publish_releases_without_marking(self):
        store = MagicMock()
        delivery = self.delivery()
        store.claim_outbox.return_value = [delivery]
        jetstream = AsyncMock()
        jetstream.publish.side_effect = OSError("unavailable")
        await publish_outbox_once(store, jetstream, AsyncMock())
        store.release_outbox.assert_called_once_with(delivery, "OSError")
        store.mark_outbox_published.assert_not_called()

    async def test_lineage_uses_existing_ingestion_lane(self):
        identity = uuid4()
        evidence = CollectionDefinition(
            record_id=identity, collection_id=identity, visibility="public",
            recorded_at=datetime.now(UTC), specification={"page_limit": 1},
        )
        delivery = self.delivery("lineage", evidence.model_dump(mode="json"))
        ingestion, jetstream = AsyncMock(), AsyncMock()
        await publish_delivery(delivery, jetstream, ingestion)
        job = ingestion.enqueue.await_args.args[0]
        self.assertEqual(job.lineage, evidence)
        self.assertEqual(job.kind, "lineage")
        jetstream.publish.assert_not_awaited()

    async def test_receipt_loop_never_treats_pending_or_failed_delivery_as_commit(self):
        from periplus.crawl.runtime.frontier_outbox import reconcile_receipts_once
        from periplus.ingestion.queue import IngestionState
        identity = uuid4()
        evidence = CollectionDefinition(record_id=identity, collection_id=identity, visibility="public",
                                        recorded_at=datetime.now(UTC), specification={})
        delivery = self.delivery("lineage", evidence.model_dump(mode="json"))
        for status in ("pending", "failed"):
            store = MagicMock()
            store.claim_ingestion_receipts.return_value = [delivery]
            state = IngestionState(job=delivery.ingestion_job(), status=status, updated_at=datetime.now(UTC),
                                   error="unavailable" if status == "failed" else None)
            ingestion = AsyncMock()
            ingestion.reconcile.return_value = state
            await reconcile_receipts_once(store, ingestion)
            store.record_ingestion_receipt.assert_not_called()
            store.defer_ingestion_receipt.assert_called_once_with(delivery, f"ingestion_{status}")
