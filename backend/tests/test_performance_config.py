from __future__ import annotations

import unittest
from unittest.mock import patch

from config.performance import (
    GRAPH_CONSUMER_MAX_ACK_PENDING,
    catalogue_read_pool_size,
    duckdb_memory_limit,
    duckdb_threads,
)
from runtime.resource_governor import ResourceLimits


class PerformanceConfigTests(unittest.TestCase):
    def test_catalogue_fairness_is_derived_from_one_maximum(self) -> None:
        limits = ResourceLimits(catalogue=8, object_io=64)

        self.assertEqual(limits.catalogue_critical_reserve, 1)
        self.assertEqual(limits.catalogue_noncritical_reserve, 1)
        self.assertEqual(limits.catalogue_backfill_max, 2)
        self.assertEqual(limits.object_read, 64)
        self.assertEqual(limits.object_write, 64)

    def test_single_catalogue_lane_cannot_deadlock_on_reservations(self) -> None:
        limits = ResourceLimits(catalogue=1, object_io=1)

        self.assertEqual(limits.catalogue_critical_reserve, 0)
        self.assertEqual(limits.catalogue_noncritical_reserve, 0)
        self.assertEqual(limits.catalogue_backfill_max, 1)

    def test_local_sizing_is_bounded_and_replica_friendly(self) -> None:
        with patch("config.performance.os.process_cpu_count", return_value=32):
            self.assertEqual(duckdb_threads(), 2)
            self.assertEqual(catalogue_read_pool_size(), 4)

        self.assertGreater(GRAPH_CONSUMER_MAX_ACK_PENDING, 1)

    def test_duckdb_memory_is_derived_with_safe_bounds(self) -> None:
        with patch("config.performance._cgroup_memory_limit", return_value=4 * 1024**3):
            self.assertEqual(duckdb_memory_limit(), "512MB")
        with patch("config.performance._cgroup_memory_limit", return_value=128 * 1024**3):
            self.assertEqual(duckdb_memory_limit(), "2048MB")


if __name__ == "__main__":
    unittest.main()
