"""The single capture lane is provisioned at startup and validated on reuse."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from nats.js.api import AckPolicy, ConsumerConfig, DiscardPolicy, RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import NotFoundError
from periplus.crawl.runtime.frontier_queue import (
    CAPTURE_STREAM, CAPTURE_SUBJECT, CAPTURE_CONSUMER, CAPTURE_MAX_PENDING,
    CAPTURE_ACK_WAIT, ensure_capture_queue,
)


class FrontierQueueTests(unittest.IsolatedAsyncioTestCase):
    def configured(self):
        js = AsyncMock()
        js.stream_info.return_value = SimpleNamespace(config=StreamConfig(
            name=CAPTURE_STREAM, subjects=[CAPTURE_SUBJECT], storage=StorageType.FILE,
            retention=RetentionPolicy.WORK_QUEUE, discard=DiscardPolicy.NEW,
            num_replicas=1, max_bytes=32 * 1024 * 1024,
        ))
        js.consumer_info.return_value = SimpleNamespace(config=ConsumerConfig(
            durable_name=CAPTURE_CONSUMER, filter_subject=CAPTURE_SUBJECT,
            ack_policy=AckPolicy.EXPLICIT, ack_wait=CAPTURE_ACK_WAIT,
            max_ack_pending=CAPTURE_MAX_PENDING, max_deliver=-1,
        ))
        return js

    async def test_existing_contract_needs_no_provisioning(self):
        js = self.configured()
        await ensure_capture_queue(js)
        js.add_stream.assert_not_awaited()
        js.add_consumer.assert_not_awaited()

    async def test_missing_contract_is_installed_and_read_back(self):
        js = self.configured()
        js.stream_info.side_effect = [NotFoundError(), js.stream_info.return_value]
        js.consumer_info.side_effect = [NotFoundError(), js.consumer_info.return_value]
        await ensure_capture_queue(js)
        js.add_stream.assert_awaited_once()
        js.add_consumer.assert_awaited_once()
        self.assertEqual(js.add_stream.await_args.kwargs["config"].discard, DiscardPolicy.NEW)

    async def test_contract_drift_fails_without_reconfiguration(self):
        js = self.configured()
        js.consumer_info.return_value.config.max_ack_pending = 100000
        with self.assertRaisesRegex(RuntimeError, "max_ack_pending"):
            await ensure_capture_queue(js)
        js.add_consumer.assert_not_awaited()
