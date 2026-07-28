from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from nats.js.errors import NotFoundError

from atlas.ingestion.queue import (
    DURABLE,
    STREAM,
    ensure_repository_consumer,
    repository_consumer_config,
)


class IngestionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_durable_consumer_is_not_reconfigured(self) -> None:
        expected = repository_consumer_config()
        existing = SimpleNamespace(config=expected)
        jetstream = SimpleNamespace(
            consumer_info=AsyncMock(return_value=existing),
            add_consumer=AsyncMock(),
        )

        await ensure_repository_consumer(jetstream)

        jetstream.consumer_info.assert_awaited_once_with(STREAM, DURABLE)
        jetstream.add_consumer.assert_not_awaited()

    async def test_missing_durable_consumer_is_created_once(self) -> None:
        expected = repository_consumer_config()
        created = SimpleNamespace(config=expected)
        jetstream = SimpleNamespace(
            consumer_info=AsyncMock(side_effect=NotFoundError()),
            add_consumer=AsyncMock(return_value=created),
        )

        await ensure_repository_consumer(jetstream)

        jetstream.add_consumer.assert_awaited_once_with(
            STREAM,
            config=expected,
        )

    async def test_mutable_delivery_limits_are_reconciled_at_startup(self) -> None:
        expected = repository_consumer_config()
        existing = SimpleNamespace(
            config=replace(expected, max_ack_pending=1024)
        )
        reconciled = SimpleNamespace(config=expected)
        jetstream = SimpleNamespace(
            consumer_info=AsyncMock(return_value=existing),
            add_consumer=AsyncMock(return_value=reconciled),
        )

        await ensure_repository_consumer(jetstream)

        jetstream.add_consumer.assert_awaited_once_with(
            STREAM,
            config=expected,
        )


if __name__ == "__main__":
    unittest.main()
