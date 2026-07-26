from __future__ import annotations

import unittest
from unittest.mock import patch

from config.performance import (
    GRAPH_CONSUMER_MAX_ACK_PENDING,
    INGEST_BATCH_MAX_ITEMS,
    INGESTION_CONSUMER_MAX_ACK_PENDING,
    INGESTION_QUACK_CLIENTS,
    MATERIALIZATION_BOOTSTRAP_CONCURRENCY,
    MATERIALIZATION_QUACK_CLIENTS,
    duckdb_memory_limit,
    duckdb_threads,
    materialization_duckdb_memory_limit,
)


class PerformanceConfigTests(unittest.TestCase):
    def test_local_sizing_uses_bounded_managed_clients(self) -> None:
        self.assertEqual(INGESTION_QUACK_CLIENTS, 4)
        self.assertEqual(MATERIALIZATION_QUACK_CLIENTS, 8)
        self.assertEqual(MATERIALIZATION_BOOTSTRAP_CONCURRENCY, 3)
        with patch("config.performance.os.process_cpu_count", return_value=32):
            self.assertEqual(duckdb_threads(), 2)

        self.assertGreater(GRAPH_CONSUMER_MAX_ACK_PENDING, 1)
        self.assertEqual(
            INGESTION_CONSUMER_MAX_ACK_PENDING,
            INGESTION_QUACK_CLIENTS * INGEST_BATCH_MAX_ITEMS,
        )

    def test_duckdb_memory_is_derived_with_safe_bounds(self) -> None:
        with patch("config.performance._cgroup_memory_limit", return_value=4 * 1024**3):
            self.assertEqual(duckdb_memory_limit(), "256MB")
            self.assertEqual(materialization_duckdb_memory_limit(), "256MB")
        with patch("config.performance._cgroup_memory_limit", return_value=128 * 1024**3):
            self.assertEqual(duckdb_memory_limit(), "512MB")
            self.assertEqual(materialization_duckdb_memory_limit(), "1024MB")


if __name__ == "__main__":
    unittest.main()
