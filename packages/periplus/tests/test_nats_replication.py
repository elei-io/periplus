"""Replica configuration reaches every coordination store and rejects drift."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from nats.js.api import StorageType
from nats.js.errors import BucketNotFoundError

from periplus.crawl.runtime.domain_pacing import ensure_domain_pacing_storage
from periplus.crawl.runtime.frontier_queue import ensure_crawler_presence
from periplus.platform.config.environment import ConfigurationError
from periplus.platform.messaging.catalogue_workers import ensure_catalogue_worker_storage
from periplus.platform.messaging.leases import ensure_operation_lease_storage
from periplus.platform.messaging.topology import operational_replicas


class OperationalReplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_reopen_and_reject_replica_drift(self):
        for ensure in (ensure_domain_pacing_storage, ensure_crawler_presence,
                       ensure_catalogue_worker_storage, ensure_operation_lease_storage):
            with self.subTest(store=ensure.__name__), patch.dict(os.environ, {"PERIPLUS_NATS_OPERATIONAL_REPLICAS": "3"}):
                js = AsyncMock()
                js.key_value.side_effect = BucketNotFoundError()
                bucket = AsyncMock()

                async def create(config):
                    self.assertEqual(config.replicas, 3)
                    bucket.status.return_value = SimpleNamespace(stream_info=SimpleNamespace(config=SimpleNamespace(
                        storage=StorageType.FILE, max_msgs_per_subject=1,
                        max_age=config.ttl, max_bytes=config.max_bytes, num_replicas=3,
                    )))
                    return bucket

                js.create_key_value.side_effect = create
                self.assertIs(await ensure(js), bucket)
                js.key_value.side_effect = None
                js.key_value.return_value = bucket
                self.assertIs(await ensure(js), bucket)
                js.create_key_value.assert_awaited_once()
                bucket.status.return_value.stream_info.config.num_replicas = 1
                with self.assertRaises(RuntimeError):
                    await ensure(js)
                js.update_stream.assert_not_awaited()
                js.delete_stream.assert_not_awaited()

    def test_replica_bounds(self):
        for value in ("1", "3", "5"):
            with patch.dict(os.environ, {"PERIPLUS_NATS_OPERATIONAL_REPLICAS": value}):
                self.assertEqual(operational_replicas(), int(value))
        for value in ("0", "-1", "6"):
            with patch.dict(os.environ, {"PERIPLUS_NATS_OPERATIONAL_REPLICAS": value}):
                with self.assertRaises(ConfigurationError):
                    operational_replicas()
