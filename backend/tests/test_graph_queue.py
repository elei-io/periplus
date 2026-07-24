from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from nats.js.api import ConsumerConfig, KeyValueConfig, StorageType, StreamConfig
from nats.js.errors import NotFoundError

from runtime.graph_queue import _bucket, _ensure_consumer


class FakeBucket:
    def __init__(self, config: StreamConfig) -> None:
        self.config = config

    async def status(self):
        return SimpleNamespace(
            stream_info=SimpleNamespace(config=self.config)
        )


class FakeJetStream:
    def __init__(self, bucket: FakeBucket) -> None:
        self.bucket = bucket
        self.updated: StreamConfig | None = None

    async def key_value(self, _name: str) -> FakeBucket:
        return self.bucket

    async def update_stream(self, *, config: StreamConfig) -> None:
        self.updated = config


class GraphQueueStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_durable_consumer_is_not_reconfigured(self) -> None:
        expected = ConsumerConfig(durable_name="existing")
        existing = SimpleNamespace(config=expected)
        jetstream = SimpleNamespace(
            consumer_info=AsyncMock(return_value=existing),
            add_consumer=AsyncMock(),
        )

        returned = await _ensure_consumer(jetstream, expected)

        self.assertIs(returned, existing)
        jetstream.consumer_info.assert_awaited_once_with(
            "ATLAS_GRAPH_WORK",
            "existing",
        )
        jetstream.add_consumer.assert_not_awaited()

    async def test_missing_durable_consumer_is_created_once(self) -> None:
        expected = ConsumerConfig(durable_name="missing")
        created = SimpleNamespace(config=expected)
        jetstream = SimpleNamespace(
            consumer_info=AsyncMock(side_effect=NotFoundError()),
            add_consumer=AsyncMock(return_value=created),
        )

        returned = await _ensure_consumer(jetstream, expected)

        self.assertIs(returned, created)
        jetstream.add_consumer.assert_awaited_once_with(
            "ATLAS_GRAPH_WORK",
            config=expected,
        )

    async def test_bucket_rejects_an_unbounded_contract(self) -> None:
        actual = StreamConfig(
            name="KV_atlas_graph_workers",
            max_msgs_per_subject=1,
            storage=StorageType.FILE,
            num_replicas=1,
        )
        jetstream = FakeJetStream(FakeBucket(actual))

        with self.assertRaisesRegex(ValueError, "requires positive max_bytes"):
            await _bucket(
                jetstream,
                KeyValueConfig(
                    bucket="atlas_graph_workers",
                    history=1,
                    storage=StorageType.FILE,
                    replicas=1,
                ),
            )

    async def test_existing_bucket_adopts_bounded_retention(self) -> None:
        actual = StreamConfig(
            name="KV_atlas_graph_workers",
            max_msgs_per_subject=1,
            max_age=0,
            max_bytes=256,
            storage=StorageType.FILE,
            num_replicas=1,
        )
        bucket = FakeBucket(actual)
        jetstream = FakeJetStream(bucket)

        returned = await _bucket(
            jetstream,
            KeyValueConfig(
                bucket="atlas_graph_workers",
                history=1,
                ttl=60,
                max_bytes=512,
                storage=StorageType.FILE,
                replicas=1,
            ),
        )

        self.assertIs(returned, bucket)
        assert jetstream.updated is not None
        self.assertEqual(jetstream.updated.max_age, 60)
        self.assertEqual(jetstream.updated.max_bytes, 512)


if __name__ == "__main__":
    unittest.main()
