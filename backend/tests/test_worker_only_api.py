from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.app import app
from cli.config import api_url, init_config


class WorkerOnlyApiTests(unittest.TestCase):
    def test_every_action_is_post_only_and_calibrate_has_no_legacy_route(self) -> None:
        paths = app.openapi()["paths"]
        for path in ("/search/", "/index/", "/crawl/", "/schema/", "/extract/", "/calibrate/"):
            self.assertEqual(set(paths[path]), {"post"})
            self.assertEqual(paths[path]["post"]["responses"]["202"]["description"], "Successful Response")
        self.assertNotIn("/crawl-policies/calibrate", paths)

    def test_task_run_read_endpoints_are_shared(self) -> None:
        paths = app.openapi()["paths"]
        self.assertIn("get", paths["/task-runs/{run_id}"])
        self.assertIn("get", paths["/task-runs/{run_id}/progress"])
        self.assertIn("get", paths["/task-runs/{run_id}/result"])
        self.assertIn("post", paths["/task-runs/{run_id}/cancel"])
        self.assertIn("get", paths["/task-runs/operations/summary"])
        self.assertIn("get", paths["/operations/metrics"])

    def test_cli_config_walks_to_nearest_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "one" / "two"
            child.mkdir(parents=True)
            with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.cwd", return_value=root):
                init_config("https://atlas.example.com/")
            with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.cwd", return_value=child):
                self.assertEqual(api_url(), "https://atlas.example.com")

    def test_cli_environment_overrides_project_config(self) -> None:
        with patch.dict(os.environ, {"ATLAS_API_URL": "https://override.example.com/"}, clear=True):
            self.assertEqual(api_url(), "https://override.example.com")
