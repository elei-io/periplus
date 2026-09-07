import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from periplus.crawl.acquisition.destination import public_destination_url, DestinationRejected, DestinationUnavailable


class PublicDestinationTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonpublic_literals_never_resolve_or_proceed(self):
        with patch.object(asyncio.get_running_loop(), 'getaddrinfo', AsyncMock()) as resolver:
            for url in ('http://127.0.0.1/', 'http://10.0.0.1/', 'http://169.254.169.254/',
                        'http://[::1]/', 'http://[::ffff:127.0.0.1]/', 'http://224.0.0.1/', 'http://[ff02::1]/'):
                with self.subTest(url=url), self.assertRaises(DestinationRejected):
                    await public_destination_url(url)
            self.assertEqual(await public_destination_url('https://1.1.1.1/'), 'https://1.1.1.1/')
            resolver.assert_not_awaited()

    async def test_every_resolved_address_must_be_public(self):
        def address(value):
            return (0, 0, 0, '', (value, 443))
        with patch.object(asyncio.get_running_loop(), 'getaddrinfo', AsyncMock()) as resolver:
            for addresses in ([], [address('1.1.1.1'), address('192.168.1.1')], [address('::1')]):
                resolver.return_value = addresses
                with self.assertRaises(DestinationRejected):
                    await public_destination_url('https://example.com/')
            resolver.return_value = [address('1.1.1.1'), address('2606:4700:4700::1111')]
            self.assertEqual(await public_destination_url('https://example.com/#fragment'), 'https://example.com/')
            for failure in (OSError('DNS'), TimeoutError()):
                resolver.side_effect = failure
                with self.assertRaises(DestinationUnavailable):
                    await public_destination_url('https://example.com/')

    async def test_cancelled_callers_do_not_free_unfinished_resolver_capacity(self):
        release = asyncio.Event()
        async def resolve(*args, **kwargs):
            await release.wait()
            return [(0,0,0,'',('1.1.1.1',443))]
        with patch.object(asyncio.get_running_loop(), 'getaddrinfo', AsyncMock(side_effect=resolve)) as resolver:
            callers = [asyncio.create_task(public_destination_url('https://example.com/')) for _ in range(4)]
            while resolver.await_count < 4:
                await asyncio.sleep(0)
            for caller in callers:
                caller.cancel()
            await asyncio.gather(*callers, return_exceptions=True)
            with self.assertRaisesRegex(DestinationUnavailable, 'capacity'):
                await public_destination_url('https://example.com/')
            self.assertEqual(resolver.await_count, 4)
            release.set()
            from periplus.crawl.acquisition.destination import _lookups
            while _lookups[asyncio.get_running_loop()]:
                await asyncio.sleep(0)
            self.assertEqual(await public_destination_url('https://example.com/'), 'https://example.com/')
