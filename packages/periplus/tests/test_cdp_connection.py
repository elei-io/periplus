import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from playwright.async_api import Error
from periplus.crawl.acquisition.capture import connect_cdp
from periplus.crawl.acquisition.errors import CdpUnavailable, PlaywrightRuntimeLost


class CdpConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_is_reused_and_closed_without_creating_a_page(self):
        browser = AsyncMock()
        connect = AsyncMock(return_value=browser)
        playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
        with patch('periplus.crawl.acquisition.capture.get_str', return_value='http://cdp.example'):
            async with connect_cdp(playwright) as actual:
                self.assertIs(actual, browser)
                browser.close.assert_not_awaited()
        connect.assert_awaited_once_with('http://cdp.example', timeout=5000)
        browser.new_context.assert_not_awaited()
        browser.close.assert_awaited_once()

    async def test_cancellation_after_connect_still_closes_the_owned_connection(self):
        browser = AsyncMock()
        playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=AsyncMock(return_value=browser)))
        with self.assertRaises(asyncio.CancelledError):
            async with connect_cdp(playwright):
                raise asyncio.CancelledError()
        browser.close.assert_awaited_once()

    async def test_connection_failures_are_unavailability_but_driver_loss_remains_fatal(self):
        for error in (OSError('offline'), TimeoutError(), Error('endpoint failed')):
            playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=AsyncMock(side_effect=error)))
            with self.assertRaises(CdpUnavailable):
                async with connect_cdp(playwright):
                    self.fail('unavailable connection yielded')
        playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=AsyncMock(
            side_effect=Error('Connection closed while reading from the driver'))))
        with self.assertRaises(PlaywrightRuntimeLost):
            async with connect_cdp(playwright):
                self.fail('lost driver yielded')
