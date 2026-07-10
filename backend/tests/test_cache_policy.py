from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from actions.shared.cache import CacheOptions, resolve_cache_policy


class CachePolicyTests(unittest.TestCase):
    def test_defaults_are_finite_and_prefer_reuse(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            resolved = resolve_cache_policy(crawl_policy_config=None, request=None)

        self.assertEqual(resolved.mode, "prefer")
        self.assertEqual(resolved.max_age_seconds, 120)
        self.assertIsNone(resolved.stale_if_error_seconds)
        self.assertTrue(resolved.reads_cache)
        self.assertTrue(resolved.stores_result)

    def test_request_overrides_policy_without_affecting_other_defaults(self) -> None:
        resolved = resolve_cache_policy(
            crawl_policy_config={
                "cache": {
                    "mode": "prefer",
                    "max_age_seconds": 600,
                    "stale_if_error_seconds": 3600,
                }
            },
            request=CacheOptions(mode="refresh", max_age_seconds=30),
        )

        self.assertEqual(resolved.mode, "refresh")
        self.assertEqual(resolved.max_age_seconds, 30)
        self.assertEqual(resolved.stale_if_error_seconds, 3600)
        self.assertFalse(resolved.reads_cache)
        self.assertTrue(resolved.stores_result)

    def test_no_store_reads_and_writes_nothing(self) -> None:
        resolved = resolve_cache_policy(
            crawl_policy_config=None,
            request=CacheOptions(mode="no_store"),
        )

        self.assertFalse(resolved.reads_cache)
        self.assertFalse(resolved.stores_result)

    def test_stale_window_cannot_be_shorter_than_fresh_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "stale_if_error_seconds"):
            resolve_cache_policy(
                crawl_policy_config=None,
                request=CacheOptions(
                    max_age_seconds=120,
                    stale_if_error_seconds=60,
                ),
            )


if __name__ == "__main__":
    unittest.main()
