from __future__ import annotations

import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from runtime.graph_outbox import publish_outbox_once
from runtime.graph_store import OutboxDelivery


class GraphOutboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_puback_precedes_published_marker(self) -> None:
        delivery = OutboxDelivery(
            id=uuid4(),
            message_id="crawl:one:g1",
            subject="atlas.graph.crawl",
            payload={"crawl_request_id": str(uuid4()), "generation": 1},
            claim_token=uuid4(),
        )
        calls: list[str] = []
        store = AsyncMock()
        store.claim_outbox.return_value = [delivery]
        store.mark_outbox_published.side_effect = (
            lambda _delivery: calls.append("marked")
        )
        jetstream = AsyncMock()
        jetstream.publish.side_effect = lambda *_args, **_kwargs: calls.append(
            "published"
        )

        worked = await publish_outbox_once(store, jetstream)

        self.assertEqual(worked, 1)
        self.assertEqual(calls, ["published", "marked"])
        jetstream.publish.assert_awaited_once()
        self.assertEqual(
            jetstream.publish.await_args.kwargs["headers"],
            {"Nats-Msg-Id": delivery.message_id},
        )

    async def test_failed_publish_releases_claim_without_marking(self) -> None:
        delivery = OutboxDelivery(
            id=uuid4(),
            message_id="crawl:one:g1",
            subject="atlas.graph.crawl",
            payload={"crawl_request_id": str(uuid4()), "generation": 1},
            claim_token=uuid4(),
        )
        store = AsyncMock()
        store.claim_outbox.return_value = [delivery]
        jetstream = AsyncMock()
        jetstream.publish.side_effect = OSError("NATS unavailable")

        worked = await publish_outbox_once(store, jetstream)

        self.assertEqual(worked, 1)
        store.release_outbox.assert_awaited_once()
        store.mark_outbox_published.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
