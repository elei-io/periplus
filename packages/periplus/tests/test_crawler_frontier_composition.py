"""The crawler process starts only against the installed frontier contract."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class CrawlerCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_validation_precedes_delivery_and_capture(self):
        from periplus.crawl import crawler
        endpoints = MagicMock()
        endpoints.close = AsyncMock()
        store = MagicMock()
        store.validate_installed.side_effect = RuntimeError("frontier not installed")
        with patch.object(crawler, "install_signal_handlers"), \
             patch.object(crawler, "WorkerEndpoints", return_value=endpoints), \
             patch.object(crawler, "FrontierStore", return_value=store), \
             patch.object(crawler, "connect_nats", AsyncMock()) as connect:
            with self.assertRaisesRegex(RuntimeError, "frontier not installed"):
                await crawler.run()
        connect.assert_not_awaited()
        endpoints.close.assert_awaited_once()
