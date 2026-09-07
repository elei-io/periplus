"""Delivery preflight reads only already-owned infrastructure handles."""
import unittest
from unittest.mock import AsyncMock

from periplus.ingestion.queue import IngestionQueueClient, STREAM


class IngestionAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        client = IngestionQueueClient()
        client.client = AsyncMock()
        client.jetstream = AsyncMock()
        client.results = AsyncMock()
        return client

    async def test_existing_handles_are_probed_without_mutation(self):
        client = self.client()
        await client.check_available()
        client.client.flush.assert_awaited_once_with(timeout=2)
        client.jetstream.stream_info.assert_awaited_once_with(STREAM)
        client.results.status.assert_awaited_once()
        client.jetstream.add_consumer.assert_not_called()
        client.jetstream.add_stream.assert_not_called()
        client.jetstream.publish.assert_not_called()
        client.results.put.assert_not_called()

    async def test_disconnected_or_missing_delivery_storage_fails_closed(self):
        with self.assertRaises(RuntimeError):
            await IngestionQueueClient().check_available()
        for target in ('client', 'jetstream', 'results'):
            client = self.client()
            operation = {'client': 'flush', 'jetstream': 'stream_info', 'results': 'status'}[target]
            getattr(getattr(client, target), operation).side_effect = OSError('unavailable')
            with self.assertRaises(OSError):
                await client.check_available()
