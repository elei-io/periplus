import asyncio
import threading
import unittest
from unittest.mock import Mock
from periplus.crawl.control.collections.results import CrawlResults
from periplus.crawl.control.collections.history import HistoryUnavailable


class CollectionReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_drains_native_read_before_client_is_reused(self):
        entered,release=threading.Event(),threading.Event()
        client=Mock()
        def read(*args,**kwargs):
            entered.set()
            release.wait(5)
            return {'data':[],'meta':[]}
        client.query.side_effect=read
        reader=CrawlResults(client,None)
        task=asyncio.create_task(reader._read('SELECT 1'))
        await asyncio.to_thread(entered.wait,2)
        task.cancel()
        await asyncio.sleep(0)
        self.assertTrue(reader._slot.locked())
        release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(reader._slot.locked())

    async def test_admission_is_bounded(self):
        reader=CrawlResults(Mock(),None)
        reader._pending=8
        with self.assertRaises(HistoryUnavailable):await reader._read('SELECT 1')
        reader.client.query.assert_not_called()
