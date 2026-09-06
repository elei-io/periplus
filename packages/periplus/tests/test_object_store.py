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


if __name__ == "__main__":
    unittest.main()
