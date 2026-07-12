from __future__ import annotations

import unittest

from ducklake_client import DuckLakeError

from workers.crawl import _is_transient_catalogue_failure


class CrawlWorkerFailureTests(unittest.TestCase):
    def test_ducklake_query_failure_is_retryable(self) -> None:
        self.assertTrue(
            _is_transient_catalogue_failure(DuckLakeError("DuckLake sql_dicts failed"))
        )

    def test_unrelated_acquisition_failure_remains_terminal(self) -> None:
        self.assertFalse(_is_transient_catalogue_failure(RuntimeError("browser failed")))
