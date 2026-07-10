from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from crawl_policies.models import CrawlPolicy
from crawl_policies.permits import _policy_limit
from crawl_policies.service import find_crawl_policy_snapshot_for_url, update_crawl_policy
from tasks.executor import _worker_concurrency


class CrawlPermitConfigTests(unittest.TestCase):
    def test_frozen_policy_matching_does_not_read_mutable_policy_state(self) -> None:
        policy_id = uuid4()
        snapshots = [
            {
                "id": str(policy_id),
                "revision": 4,
                "metric_slug": "example-policy",
                "domain_group": "example",
                "match": "https://example.com/articles/*",
                "config": {"mode": "static", "max_concurrency": 2},
                "matcher": {
                    "scheme": "https",
                    "host": "example.com",
                    "path_pattern": "/articles/*",
                    "match_type": "glob",
                    "priority": 10,
                },
            }
        ]
        mutable_policy_config = {"mode": "app", "max_concurrency": 99}

        matched = find_crawl_policy_snapshot_for_url(
            snapshots,
            url="https://example.com/articles/queued-run",
        )

        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertEqual(matched.id, policy_id)
        self.assertEqual(matched.revision, 4)
        self.assertEqual(matched.config["mode"], "static")
        self.assertNotEqual(matched.config, mutable_policy_config)

    def test_policy_update_increments_revision(self) -> None:
        policy = CrawlPolicy(
            id=uuid4(),
            match="https://example.com/*",
            config={},
            revision=4,
        )

        session = MagicMock()
        session.scalar.return_value = policy
        update_crawl_policy(
            session,
            policy=policy,
            config={"mode": "static"},
        )

        self.assertEqual(policy.revision, 5)

    def test_worker_concurrency_is_bounded_and_defaults_to_four(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_worker_concurrency(), 4)
        with patch.dict(os.environ, {"ATLAS_WORKER_CONCURRENCY": "0"}, clear=True):
            self.assertEqual(_worker_concurrency(), 1)

    def test_policy_limit(self) -> None:
        policy = CrawlPolicy(
            id=uuid4(),
            match="https://example.com/*",
            config={"max_concurrency": 2},
        )
        self.assertEqual(_policy_limit(policy), 2)

    def test_policy_limit_accepts_numeric_strings(self) -> None:
        policy = CrawlPolicy(
            id=uuid4(),
            match="https://example.com/*",
            config={"max_concurrency": "3"},
        )
        self.assertEqual(_policy_limit(policy), 3)
