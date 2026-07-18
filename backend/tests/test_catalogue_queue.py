from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nats.js.errors import NotFoundError
from nats.js.api import DiscardPolicy

from runtime.catalogue_queue import (
    DEAD_LETTER_STREAM,
    DEAD_LETTER_SUBJECTS,
    WORK_STREAM,
    WORK_SUBJECTS,
    ensure_catalogue_work_stream,
    ensure_dead_letter_stream,
)


class FakeJetStream:
    def __init__(self) -> None:
        self.streams = {}

    async def stream_info(self, name: str):
        if name not in self.streams:
            raise NotFoundError
        return SimpleNamespace(config=self.streams[name])

    async def add_stream(self, *, config) -> None:
        self.streams[config.name] = config


class CatalogueQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_stream_topology_is_concrete_and_idempotent(self) -> None:
        jetstream = FakeJetStream()
        with (
            patch(
                "runtime.catalogue_queue.get_int",
                side_effect=lambda name: {
                    "ATLAS_CATALOGUE_WORK_STREAM_REPLICAS": 1,
                    "ATLAS_CATALOGUE_WORK_MAX_BYTES": 1024,
                    "ATLAS_DEAD_LETTER_MAX_BYTES": 2048,
                }[name],
            ),
            patch("runtime.catalogue_queue.get_float", return_value=3600.0),
        ):
            await ensure_catalogue_work_stream(jetstream)
            await ensure_catalogue_work_stream(jetstream)
            await ensure_dead_letter_stream(jetstream)
            await ensure_dead_letter_stream(jetstream)

        work = jetstream.streams[WORK_STREAM]
        self.assertEqual(set(work.subjects), set(WORK_SUBJECTS))
        self.assertEqual(work.max_age, 0)
        self.assertEqual(work.max_bytes, 1024)
        self.assertEqual(work.discard, DiscardPolicy.NEW)
        dead = jetstream.streams[DEAD_LETTER_STREAM]
        self.assertEqual(set(dead.subjects), set(DEAD_LETTER_SUBJECTS))
        self.assertEqual(dead.max_age, 3600.0)


if __name__ == "__main__":
    unittest.main()
