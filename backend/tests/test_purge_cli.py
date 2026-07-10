from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from cli.app import app
from worker.process_cleanup import PlaywrightCleanupResult


class PurgeCliTests(unittest.TestCase):
    def test_purge_workers_calls_operations_api_and_reports_count(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.is_success = True
        response.json.return_value = {"deleted": 2}
        client.post.return_value = response
        context = MagicMock()
        context.__enter__.return_value = client

        with patch("cli.apps.purge._client", return_value=context):
            result = CliRunner().invoke(app, ["purge", "workers"])

        self.assertEqual(result.exit_code, 0)
        client.post.assert_called_once_with("/operations/purge/workers")
        self.assertIn("Purged 2 stale workers.", result.output)

    def test_purge_playwright_runs_local_process_cleanup(self) -> None:
        with patch(
            "cli.apps.purge.purge_orphaned_playwright_processes",
            return_value=PlaywrightCleanupResult(
                drivers_found=3,
                processes_signalled=12,
            ),
        ) as cleanup:
            result = CliRunner().invoke(app, ["purge", "playwright"])

        self.assertEqual(result.exit_code, 0)
        cleanup.assert_called_once_with(dry_run=False)
        self.assertIn(
            "Purged 3 orphaned Playwright drivers across 12 processes.",
            result.output,
        )


if __name__ == "__main__":
    unittest.main()
