from __future__ import annotations

import unittest
from unittest.mock import patch

from worker.process_cleanup import (
    ProcessSnapshot,
    find_detached_playwright_browsers,
    find_orphaned_playwright_drivers,
    purge_orphaned_playwright_processes,
)


class PlaywrightCleanupTests(unittest.TestCase):
    def test_only_task_drivers_without_worker_ancestors_are_orphaned(self) -> None:
        marker = "/atlas/playwright/driver"
        processes = [
            ProcessSnapshot(10, 1, "python -m worker.app"),
            ProcessSnapshot(20, 10, "python -c from multiprocessing.spawn import spawn_main"),
            ProcessSnapshot(30, 20, f"node {marker}/package/cli.js run-driver"),
            ProcessSnapshot(40, 1, "python -c from multiprocessing.spawn import spawn_main"),
            ProcessSnapshot(50, 40, f"node {marker}/package/cli.js run-driver"),
            ProcessSnapshot(60, 1, "python test_browser.py"),
            ProcessSnapshot(70, 60, f"node {marker}/package/cli.js run-driver"),
        ]

        orphaned = find_orphaned_playwright_drivers(
            processes,
            driver_marker=marker,
        )

        self.assertEqual([process.pid for process in orphaned], [50])

    def test_purge_signals_the_orphaned_task_subtree(self) -> None:
        marker = "/atlas/playwright/driver"
        processes = [
            ProcessSnapshot(40, 1, "python -c from multiprocessing.spawn import spawn_main"),
            ProcessSnapshot(50, 40, f"node {marker}/package/cli.js run-driver"),
            ProcessSnapshot(51, 50, "chrome --headless"),
        ]

        with (
            patch("worker.process_cleanup._processes", return_value=processes),
            patch("worker.process_cleanup._driver_marker", return_value=marker),
            patch("worker.process_cleanup._signal_existing", return_value=True) as signal_process,
            patch("worker.process_cleanup.time.sleep"),
        ):
            result = purge_orphaned_playwright_processes()

        self.assertEqual(result.drivers_found, 1)
        self.assertEqual(result.browsers_found, 0)
        self.assertEqual(result.processes_signalled, 3)
        terminated = [call.args[0] for call in signal_process.call_args_list[:3]]
        self.assertEqual(terminated, [51, 50, 40])

    def test_detached_playwright_browser_roots_are_identified(self) -> None:
        processes = [
            ProcessSnapshot(
                80,
                1,
                "Google Chrome for Testing --user-data-dir=/tmp/playwright_chromiumdev_profile-a",
            ),
            ProcessSnapshot(
                81,
                80,
                "Google Chrome for Testing --type=renderer --user-data-dir=/tmp/playwright_chromiumdev_profile-a",
            ),
            ProcessSnapshot(90, 1, "Google Chrome --user-data-dir=/tmp/normal-profile"),
        ]

        detached = find_detached_playwright_browsers(processes)

        self.assertEqual([process.pid for process in detached], [80])


if __name__ == "__main__":
    unittest.main()
