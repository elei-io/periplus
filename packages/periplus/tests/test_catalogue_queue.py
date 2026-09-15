from types import SimpleNamespace
import unittest
from unittest.mock import patch
from nats.js.api import DiscardPolicy, RetentionPolicy
from nats.js.errors import NotFoundError
from periplus.platform.messaging.catalogue_queue import WORK_STREAM, MATERIAL_STREAM, ensure_catalogue_work_stream


class FakeJetStream:
    def __init__(self):self.streams={}
    async def stream_info(self,name):
        if name not in self.streams:raise NotFoundError
        return SimpleNamespace(config=self.streams[name])
    async def add_stream(self,*,config):self.streams[config.name]=config


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_notifications_are_bounded_and_material_work_cannot_silently_expire(self):
        js=FakeJetStream()
        with patch('periplus.platform.messaging.catalogue_queue.get_int',side_effect=lambda key:1 if key.endswith('REPLICAS') else 1024):
            await ensure_catalogue_work_stream(js)
            await ensure_catalogue_work_stream(js)
        self.assertEqual(set(js.streams),{WORK_STREAM,MATERIAL_STREAM})
        work=js.streams[MATERIAL_STREAM]
        self.assertEqual(work.retention,RetentionPolicy.WORK_QUEUE)
        self.assertEqual(work.max_age,0)
        self.assertEqual(work.discard,DiscardPolicy.NEW)
        self.assertEqual(js.streams[WORK_STREAM].max_age,86400)
