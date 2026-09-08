"""Deployment limits reach real DuckDB clients and reject unbounded settings."""
import os
import unittest
from unittest.mock import patch

import duckdb
from pydantic import ValidationError

from periplus.platform.config.duckdb import connection_limits


class DuckDBLimitTests(unittest.TestCase):
    def test_unset_overrides_preserve_workload_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            defaults = {"threads": "1", "memory_limit": "128MB", "enable_external_access": "false"}
            self.assertEqual(connection_limits(defaults), defaults)
            self.assertEqual(connection_limits(), {})

    def test_explicit_limits_apply_to_real_connection(self):
        with patch.dict(os.environ, {"PERIPLUS_DUCKDB_THREADS": "3", "PERIPLUS_DUCKDB_MEMORY_LIMIT": "128MiB", "PERIPLUS_DUCKDB_MAX_TEMP_DIRECTORY_SIZE": "0B"}):
            settings = connection_limits({"threads": "1", "memory_limit": "2GB"})
        with duckdb.connect(config=settings) as connection:
            actual = connection.execute("SELECT current_setting('threads'), current_setting('memory_limit'), current_setting('max_temp_directory_size')").fetchone()
            self.assertEqual(actual, (3, "128.0 MiB", "0 bytes"))

    def test_factory_applies_overrides_before_attachment(self):
        from periplus.platform.catalogue.config import CatalogueConfig
        from periplus.platform.catalogue.connection import DuckLakeConnectionFactory
        config = CatalogueConfig("periplus", "/tmp/unused.duckdb", "/tmp/unused-lake", "ducklake")
        with patch.dict(os.environ, {"PERIPLUS_DUCKDB_THREADS": "3", "PERIPLUS_DUCKDB_MEMORY_LIMIT": "256MB", "PERIPLUS_DUCKDB_MAX_TEMP_DIRECTORY_SIZE": "128MB"}), patch("periplus.platform.catalogue.connection.duckdb.connect") as connect:
            DuckLakeConnectionFactory(config, duckdb_config={"threads": "1", "memory_limit": "2GB"}).connect(read_only=True)
            self.assertEqual(connect.call_args.kwargs["config"], {"threads": "3", "memory_limit": "256MB", "max_temp_directory_size": "128MB", "allow_unsigned_extensions": "false"})

    def test_read_only_query_runs_with_overrides_and_remains_locked(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from periplus.platform.catalogue.config import CatalogueConfig
        from periplus.platform.catalogue.connection import DuckLakeConnectionFactory
        from periplus.query.service import QueryRequest, QueryService
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = CatalogueConfig("periplus", str(root / "metadata.duckdb"), str(root / "data"), "ducklake")
            writer = DuckLakeConnectionFactory(config).connect()
            writer.close()
            with patch.dict(os.environ, {"PERIPLUS_DUCKDB_THREADS": "1", "PERIPLUS_DUCKDB_MEMORY_LIMIT": "256MiB", "PERIPLUS_DUCKDB_MAX_TEMP_DIRECTORY_SIZE": "128MiB"}):
                service = QueryService(config)
                try:
                    result = service.execute(QueryRequest(sql="SELECT 42 AS answer"))
                    self.assertEqual(result.rows, [[42]])
                    actual = service.connection.execute("SELECT current_setting('threads'), current_setting('memory_limit'), current_setting('max_temp_directory_size')").fetchone()
                    self.assertEqual(actual, (1, "256.0 MiB", "128.0 MiB"))
                    with self.assertRaises(duckdb.Error):
                        service.connection.execute("SET memory_limit='1GB'")
                    with self.assertRaises(duckdb.Error):
                        service.connection.execute("CREATE TABLE periplus.main.forbidden(i INTEGER)")
                finally:
                    service.close()

    def test_invalid_settings_fail_before_connecting(self):
        for name, values in {
            "PERIPLUS_DUCKDB_THREADS": ["0", "-1", "1025", "1.5"],
            "PERIPLUS_DUCKDB_MEMORY_LIMIT": ["0B", "-1", "unlimited", "80%", "512MB; SELECT 1"],
            "PERIPLUS_DUCKDB_MAX_TEMP_DIRECTORY_SIZE": ["-1", "unlimited", "-2GB"],
        }.items():
            for value in values:
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}), self.assertRaises(ValidationError):
                    connection_limits()
