from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from config.environment import ConfigurationError
from repository.catalogue.browser_runtime import browser_quack_runtime_from_env
from repository.catalogue.schema import CATALOGUE_SCHEMA_VERSION


class BrowserCatalogueRuntimeTests(unittest.TestCase):
    def test_quack_runtime_contains_complete_ducklake_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ,
                {
                    "ATLAS_QUACK_URI": "quack:127.0.0.1:9494",
                    "ATLAS_QUACK_TOKEN": "test-token",
                    "ATLAS_CATALOGUE_CATALOG": "duckdb",
                    "ATLAS_CATALOGUE_ROOT": temp_dir,
                    "ATLAS_REPOSITORY_ROOT": temp_dir,
                    "ATLAS_REPOSITORY_STORAGE": "disk",
                },
                clear=True,
            ):
                runtime = browser_quack_runtime_from_env()

        self.assertEqual(runtime.uri, "quack:127.0.0.1:9494")
        self.assertEqual(runtime.token, "test-token")
        self.assertEqual(runtime.catalogue_alias, "atlas")
        self.assertEqual(runtime.catalogue_schema, "main")
        self.assertEqual(runtime.metadata_schema, "main")
        self.assertEqual(runtime.catalogue_schema_version, CATALOGUE_SCHEMA_VERSION)
        self.assertIn("ATTACH 'ducklake:", runtime.attach_sql)
        self.assertIn('AS "atlas"', runtime.attach_sql)

    def test_postgres_catalogue_uses_public_metadata_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ,
                {
                    "ATLAS_QUACK_URI": "quack:catalogue.example:443",
                    "ATLAS_QUACK_TOKEN": "test-token",
                    "ATLAS_CATALOGUE_CATALOG": "postgres",
                    "ATLAS_CATALOGUE_CATALOG_DSN": "host=postgres dbname=atlas",
                    "ATLAS_CATALOGUE_ROOT": temp_dir,
                    "ATLAS_REPOSITORY_ROOT": temp_dir,
                    "ATLAS_REPOSITORY_STORAGE": "disk",
                },
                clear=True,
            ):
                runtime = browser_quack_runtime_from_env()

        self.assertEqual(runtime.metadata_schema, "public")

    def test_quack_configuration_is_required(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                ConfigurationError, "ATLAS_QUACK_URI is required"
            ):
                browser_quack_runtime_from_env()

    def test_quack_uri_must_use_quack_scheme(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ATLAS_QUACK_URI": "https://catalogue.example/quack",
                "ATLAS_QUACK_TOKEN": "test-token",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "quack: scheme"):
                browser_quack_runtime_from_env()


if __name__ == "__main__":
    unittest.main()
