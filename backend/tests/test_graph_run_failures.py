from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from api.routers.graph_runs import run_failures


class GraphRunFailureApiTests(unittest.TestCase):
    def test_returns_durable_crawl_failure_details(self) -> None:
        run_id = uuid4()
        crawl_id = uuid4()
        captured_at = datetime(2026, 7, 17, tzinfo=UTC)
        catalogue = MagicMock()
        catalogue.config.alias = "atlas"
        catalogue.config.schema = "main"
        catalogue.connection.execute.return_value.fetchall.return_value = [
            (
                crawl_id,
                "https://example.com/",
                "https://example.com/",
                None,
                "navigation_failed",
                "navigation",
                "Execution context was destroyed.",
                captured_at,
            )
        ]
        pool = MagicMock()
        pool.acquire.return_value = catalogue
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(catalogue_read_pool=pool),
            )
        )

        result = run_failures(run_id, request)

        self.assertEqual(result.total, 1)
        self.assertEqual(result.items[0].crawl_id, crawl_id)
        self.assertEqual(result.items[0].failure_code, "navigation_failed")
        self.assertEqual(
            result.items[0].failure_detail,
            "Execution context was destroyed.",
        )
        sql = catalogue.connection.execute.call_args.args[0]
        self.assertIn("outcome = 'failed'", sql)
        self.assertEqual(
            catalogue.connection.execute.call_args.args[1],
            [run_id],
        )
        pool.release.assert_called_once_with(catalogue)


if __name__ == "__main__":
    unittest.main()
