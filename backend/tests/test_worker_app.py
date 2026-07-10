from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from watchfiles import PythonFilter

from worker import app


class WorkerAppTest(unittest.TestCase):
    def test_main_runs_worker_without_reload(self) -> None:
        with patch.object(sys, "argv", ["atlas-worker"]), patch.object(app, "_run") as run:
            app.main()

        run.assert_called_once_with()

    def test_main_watches_backend_python_files_with_reload(self) -> None:
        with (
            patch.object(sys, "argv", ["atlas-worker", "--reload"]),
            patch("watchfiles.run_process") as run_process,
        ):
            app.main()

        watched_path = run_process.call_args.args[0]
        self.assertEqual(watched_path, app.Path(app.__file__).resolve().parents[1])
        self.assertIs(run_process.call_args.kwargs["target"], app._run)
        self.assertIsInstance(run_process.call_args.kwargs["watch_filter"], PythonFilter)
