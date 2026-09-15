"""Exact publication semantics when many observations share a metadata object."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import UUID

from periplus.ingestion.archive import Archive, ArchiveConflict, capture_key, event_key
from periplus.ingestion.captures import Capture
from periplus.ingestion.objects.store import FileObjectStore


class ArchiveBatchTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = FileObjectStore(self.root)
        self.archive = Archive(self.store)

    def capture(self, number, shard=0):
        return Capture(
            capture_id=UUID(int=number * 16 + shard),
            requested_url=f"https://example.com/{number}",
            completeness="unavailable",
        )

    def test_one_file_contains_64_captures_with_exact_direct_locators(self):
        captures = [self.capture(i) for i in range(1, 65)]
        events = self.archive.commit_many(captures)
        self.assertEqual(len(list(self.store.list_objects("raw/corpus/v1/"))), 1)
        fresh = Archive(FileObjectStore(self.root))
        self.assertEqual(fresh.head(0), 64)
        for capture, event in zip(captures, events):
            with patch.object(
                fresh, "receipt", side_effect=AssertionError("no index needed")
            ):
                self.assertEqual(
                    fresh.read_location(
                        capture_key(event), capture.capture_id, capture.digest
                    ),
                    capture,
                )
        self.assertEqual(list(fresh.range_events(0, 31, 35)), events[30:35])

    def test_lost_ack_and_partial_batch_retry_keep_original_positions(self):
        captures = [self.capture(i) for i in range(1, 5)]
        original = self.archive._put

        def lost(key, value):
            original(key, value)
            raise ConnectionError("lost reply")

        with patch.object(self.archive, "_put", side_effect=lost):
            with self.assertRaises(ConnectionError):
                self.archive.commit_many(captures[:3])
        fresh = Archive(FileObjectStore(self.root))
        events = fresh.commit_many(captures)
        self.assertEqual([e.sequence for e in events], [1, 2, 3, 4])
        self.assertEqual([e.segment for e in events], [1, 1, 1, 2])
        self.assertEqual(len(list(fresh.events(fresh.heads()))), 4)

    def test_separate_publishers_reject_conflicting_race_before_success(self):
        capture = self.capture(1)
        changed = capture.model_copy(
            update={"requested_url": "https://different.example/"}
        )
        barrier = Barrier(2)

        def publish(value):
            writer = Archive(FileObjectStore(self.root))
            original = writer._put
            first = True

            def race(key, batch):
                nonlocal first
                if first:
                    first = False
                    barrier.wait(timeout=10)
                return original(key, batch)

            with patch.object(writer, "_put", side_effect=race):
                try:
                    return writer.commit(value)
                except ArchiveConflict:
                    return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish, [capture, changed]))
        self.assertEqual(sum(r is not None for r in results), 1)
        fresh = Archive(FileObjectStore(self.root))
        self.assertEqual(fresh.head(0), 1)
        self.assertIn(fresh.read(capture.capture_id), [capture, changed])

    def test_snapshot_replay_keeps_later_tombstone_authoritative(self):
        captures = [self.capture(i) for i in range(1, 5)]
        self.archive.commit_many(captures)
        heads = self.archive.heads()
        self.archive.retire(captures[1].capture_id)
        fresh = Archive(FileObjectStore(self.root))
        retained = [
            fresh.read_event(e).capture_id
            for e in fresh.events(heads)
            if not fresh.retired(e.capture_id)
        ]
        self.assertEqual(retained, [c.capture_id for c in captures if c != captures[1]])
        self.assertEqual(fresh.retire(captures[1].capture_id).sequence, 5)
        self.assertEqual(fresh.head(0), 5)

    def test_corrupt_committed_batch_cannot_rebuild_index(self):
        self.archive.commit_many([self.capture(1), self.capture(2)])
        key = event_key(0, 1)
        self.store.delete(key)
        self.store.put_if_absent(key, BytesIO(b"not a Zstd frame"))
        fresh = Archive(FileObjectStore(self.root))
        with self.assertRaises(Exception):
            fresh.receipt(self.capture(1).capture_id)

    def test_directory_fanout_is_bounded_and_logical_sequences_remain_dense(self):
        self.assertIn("/0000000000/", event_key(0, 4096))
        self.assertIn("/0000000001/", event_key(0, 4097))
        first = self.archive.commit_many([self.capture(i) for i in range(1, 8)])
        second = self.archive.commit_many([self.capture(i) for i in range(8, 10)])
        self.assertEqual(list(self.archive.range_events(0, 6, 9)), first[5:] + second)


if __name__ == "__main__":
    unittest.main()
