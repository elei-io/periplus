from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.app import app
from cli.config import api_url, init_config


class WorkerOnlyApiTests(unittest.TestCase):
    def test_action_routes_are_removed_by_graph_cutover(self) -> None:
        paths = app.openapi()["paths"]
        for path in (
            "/search/",
            "/index/",
            "/crawl/",
            "/schema/",
            "/extract/",
            "/calibrate/",
        ):
            self.assertNotIn(path, paths)
        self.assertNotIn("/crawl-policies/calibrate", paths)

    def test_graph_run_endpoints_replace_task_run_routes(self) -> None:
        paths = app.openapi()["paths"]
        self.assertIn("post", paths["/crawl-graphs/{graph_id}/runs"])
        self.assertIn("get", paths["/graph-runs/{run_id}"])
        self.assertIn("get", paths["/graph-runs/{run_id}/failures"])
        self.assertNotIn("/graph-runs/materialization-definitions", paths)
        self.assertIn("post", paths["/graph-runs/{run_id}/cancel"])
        self.assertFalse(any(path.startswith("/task-runs") for path in paths))

    def test_catalogue_materialization_api_has_no_superseded_routes(self) -> None:
        paths = app.openapi()["paths"]
        self.assertIn("get", paths["/catalogue/materializations/{materialization_id}"])
        self.assertIn(
            "patch",
            paths["/catalogue/materializations/{materialization_id}"],
        )
        self.assertNotIn(
            "/catalogue/materializations/{materialization_id}/rebuild", paths
        )
        self.assertIn(
            "delete", paths["/catalogue/materializations/{materialization_id}"]
        )
        self.assertNotIn("/catalogue/queries/{query_id}/materialization", paths)
        self.assertIn(
            "put", paths["/catalogue/views/{view_reference_id}/materialization"]
        )
        self.assertNotIn("/catalogue/views/{reference_id}/recover-source-change", paths)
        self.assertFalse(any(path.startswith("/materialized-views") for path in paths))

    def test_cli_config_walks_to_nearest_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "one" / "two"
            child.mkdir(parents=True)
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("pathlib.Path.cwd", return_value=root),
            ):
                init_config("https://atlas.example.com/")
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("pathlib.Path.cwd", return_value=child),
            ):
                self.assertEqual(api_url(), "https://atlas.example.com")

    def test_cli_environment_overrides_project_config(self) -> None:
        with patch.dict(
            os.environ, {"ATLAS_API_URL": "https://override.example.com/"}, clear=True
        ):
            self.assertEqual(api_url(), "https://override.example.com")
