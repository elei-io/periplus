from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nats.js.errors import BucketNotFoundError

from runtime.catalogue_queries import (
    CATALOGUE_QUERIES_BUCKET,
    ensure_catalogue_query_storage,
)


class FakeBucket:
    def __init__(self, config) -> None:
        self.config = config

    async def status(self):
        stream_config = SimpleNamespace(
            storage=self.config.storage,
            max_msgs_per_subject=self.config.history,
            max_age=self.config.ttl,
            max_bytes=self.config.max_bytes,
            num_replicas=self.config.replicas,
        )
        return SimpleNamespace(stream_info=SimpleNamespace(config=stream_config))


class FakeJetStream:
    def __init__(self) -> None:
        self.bucket = None

    async def key_value(self, name: str):
        if self.bucket is None:
            raise BucketNotFoundError
        self.assert_name = name
        return self.bucket

    async def create_key_value(self, *, config):
        self.bucket = FakeBucket(config)
        return self.bucket


class CatalogueQueryStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_state_bucket_is_bounded_and_idempotent(self) -> None:
        jetstream = FakeJetStream()
        with (
            patch(
                "runtime.catalogue_queries.get_int",
                return_value=64 * 1024 * 1024,
            ),
            patch(
                "runtime.catalogue_queries.get_float",
                return_value=3600.0,
            ),
        ):
            first = await ensure_catalogue_query_storage(jetstream)
            second = await ensure_catalogue_query_storage(jetstream)

        self.assertIs(first, second)
        self.assertEqual(first.config.bucket, CATALOGUE_QUERIES_BUCKET)
        self.assertEqual(first.config.max_bytes, 64 * 1024 * 1024)
        self.assertEqual(first.config.ttl, 3600.0)
        self.assertEqual(first.config.history, 1)


if __name__ == "__main__":
    unittest.main()
