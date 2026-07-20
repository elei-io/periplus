from __future__ import annotations

from types import SimpleNamespace
import unittest

from nats.js.api import KeyValueConfig, StorageType, StreamConfig

from runtime.graph_queue import _bucket


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
            name="KV_atlas_graph_runs",
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
                bucket="atlas_graph_runs",
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
