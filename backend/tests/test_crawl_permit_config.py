from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from crawl_policies.models import CrawlPolicy
from crawl_policies.permits import _policy_limit
from tasks.executor import _worker_concurrency


class CrawlPermitConfigTests(unittest.TestCase):
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
