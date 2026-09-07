from __future__ import annotations

import unittest
from unittest.mock import patch

from periplus.platform.config.performance import (
    INGEST_BATCH_MAX_ITEMS,
    INGESTION_CONSUMER_MAX_ACK_PENDING,
    INGESTION_MAX_LOCAL_CONCURRENCY,
    duckdb_memory_limit,
    duckdb_threads,
    materialization_duckdb_memory_limit,
)


class PerformanceConfigTests(unittest.TestCase):
    def test_local_sizing_uses_bounded_managed_clients(self) -> None:
        self.assertEqual(INGESTION_MAX_LOCAL_CONCURRENCY, 4)
        with patch("periplus.platform.config.performance.os.process_cpu_count", return_value=32):
            self.assertEqual(duckdb_threads(), 2)

        self.assertEqual(INGESTION_CONSUMER_MAX_ACK_PENDING, 1024)
        self.assertGreater(
            INGESTION_CONSUMER_MAX_ACK_PENDING,
            INGESTION_MAX_LOCAL_CONCURRENCY * INGEST_BATCH_MAX_ITEMS,
        )

    def test_duckdb_memory_is_derived_with_safe_bounds(self) -> None:
        with patch("periplus.platform.config.performance._cgroup_memory_limit", return_value=4 * 1024**3):
            self.assertEqual(duckdb_memory_limit(), "256MB")
            self.assertEqual(materialization_duckdb_memory_limit(), "1024MB")
        with patch("periplus.platform.config.performance._cgroup_memory_limit", return_value=128 * 1024**3):
            self.assertEqual(duckdb_memory_limit(), "512MB")
            self.assertEqual(materialization_duckdb_memory_limit(), "1024MB")


if __name__ == "__main__":
    unittest.main()
