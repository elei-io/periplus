from __future__ import annotations

import unittest
from datetime import UTC, datetime

from periplus.ingestion.objects.store import S3ObjectStore


class _ListClient:
    def list_objects_v2(self, **_options):
        return {
            "Contents": [
                {
                    "Key": "runtime/navigation/",
                    "Size": 0,
                    "LastModified": datetime(2026, 8, 14, tzinfo=UTC),
                },
                {
                    "Key": "runtime/navigation/visit/package.parquet",
                    "Size": 42,
                    "LastModified": datetime(2026, 8, 14, tzinfo=UTC),
                },
            ],
            "IsTruncated": False,
        }


class S3ObjectStoreTests(unittest.TestCase):
    def test_list_objects_omits_object_store_directory_markers(self) -> None:
        store = S3ObjectStore(_ListClient(), bucket="raw")

        objects = list(store.list_objects("runtime/navigation"))

        self.assertEqual(len(objects), 1)
        self.assertEqual(
            objects[0].key,
            "runtime/navigation/visit/package.parquet",
        )
        self.assertEqual(objects[0].size, 42)


class FileObjectStoreRetentionTests(unittest.TestCase):
    def test_delete_prunes_only_empty_ancestors_and_preserves_evidence_and_root(self):
        from io import BytesIO
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from periplus.ingestion.objects.store import FileObjectStore
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = FileObjectStore(root)
            keys = ("runtime/navigation/one/package.parquet", "runtime/navigation/two/package.parquet")
            for key in keys:
                store.put_if_absent(key, BytesIO(b"navigation"))
            store.put_if_absent("html/immutable", BytesIO(b"evidence"))
            self.assertTrue(store.delete(keys[0]))
            self.assertFalse((root / "runtime/navigation/one").exists())
            self.assertTrue(store.exists(keys[1]))
            self.assertEqual(store.delete_many((keys[1], keys[0])), 1)
            self.assertFalse((root / "runtime").exists())
            with store.open("html/immutable") as content:
                self.assertEqual(content.read(), b"evidence")
            self.assertTrue(root.is_dir())
            # A previous process may have unlinked before it could prune.
            (root / "runtime/navigation/interrupted").mkdir(parents=True)
            self.assertFalse(store.delete("runtime/navigation/interrupted/package.parquet"))
            self.assertFalse((root / "runtime").exists())

    def test_writer_recovers_when_cleanup_removes_its_empty_parent(self):
        from io import BytesIO
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import tempfile
        from unittest.mock import patch
        from periplus.ingestion.objects.store import FileObjectStore
        with TemporaryDirectory() as directory:
            store = FileObjectStore(Path(directory))
            original = tempfile.mkstemp
            calls = 0
            def racing_temporary(**kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    store.delete("runtime/navigation/shared/old.parquet")
                return original(**kwargs)
            with patch("periplus.ingestion.objects.store.tempfile.mkstemp", side_effect=racing_temporary):
                self.assertTrue(store.put_if_absent("runtime/navigation/shared/new.parquet", BytesIO(b"new")))
            self.assertEqual(calls, 2)
            with store.open("runtime/navigation/shared/new.parquet") as content:
                self.assertEqual(content.read(), b"new")
            self.assertEqual(len(list(store.list_objects("runtime/navigation"))), 1)


if __name__ == "__main__":
    unittest.main()
