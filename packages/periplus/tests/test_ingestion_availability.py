import unittest
from unittest.mock import AsyncMock, Mock
from periplus.ingestion.queue import ArchivePublisher
from periplus.platform.messaging.catalogue_queue import WORK_STREAM


class IngestionAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_reuses_handles_without_provisioning(self):
        connection=Mock()
        connection.flush=AsyncMock()
        js=connection.jetstream.return_value
        js.stream_info=AsyncMock()
        publisher=ArchivePublisher(client=connection,archive=Mock())
        await publisher.check_available()
        connection.flush.assert_awaited_once()
        js.stream_info.assert_awaited_once_with(WORK_STREAM)
        js.add_stream.assert_not_called()
        js.add_consumer.assert_not_called()
