"""Publication ordering and typed ingestion routing for the frontier relay."""
from datetime import UTC, datetime
import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from periplus.crawl.runtime.frontier_outbox import publish_delivery, publish_outbox_once
from periplus.crawl.runtime.frontier_store import FrontierDelivery


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
        store.mark_outbox_published.side_effect = lambda _, **kwargs: calls.append("marked")
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
