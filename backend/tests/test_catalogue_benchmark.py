from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from ducklake_client import DiskStorage, DuckDBCatalog

from repository.catalogue import Catalogue, CatalogueConfig
from repository.catalogue.benchmark import run_hot_path_benchmark


class CatalogueBenchmarkTests(unittest.TestCase):
    def test_empty_catalogue_reports_representative_queries_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                result = run_hot_path_benchmark(
                    catalogue,
                    samples=1,
                    query_repetitions=1,
                )

        self.assertFalse(result["representative_queries"]["available"])

    def test_representative_queries_measure_partition_and_dom_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                catalogue.connection.execute(
                    """
                    INSERT INTO atlas.main.crawls (
                        crawl_id, document_id, graph_id, graph_run_id, graph_node_id,
                        crawl_request_id, purpose, requested_url, normalized_url,
                        final_url, page_url, url_scheme, url_host, url_port,
                        url_registrable_domain, url_path, url_query, captured_at,
                        status_code, profile, crawl_profile_slug, remote_concurrency,
                        config_hash, config_json, outcome
                    ) VALUES (
                        uuid(), 'doc', uuid(), uuid(), uuid(), uuid(), 'use',
                        'https://example.com/', 'https://example.com/',
                        'https://example.com/', 'https://example.com/', 'https',
                        'example.com', 443, 'example.com', '/', '',
                        TIMESTAMPTZ '2026-07-14 12:00:00+00', 200, 'http',
                        'direct', 4, repeat('a', 64), '{}', 'success'
                    )
                    """
                )
                catalogue.connection.execute(
                    """
                    INSERT INTO atlas.main.elements VALUES
                        ('doc', 0, NULL, 1, 0, 'body', NULL, map(), '', ''),
                        ('doc', 1, 0, 1, 1, 'a', NULL,
                         map(['href'], ['/docs']), 'Docs', '')
                    """
                )
                result = run_hot_path_benchmark(
                    catalogue,
                    samples=1,
                    query_repetitions=1,
                )

        representative = result["representative_queries"]
        self.assertTrue(representative["available"])
        self.assertEqual(
            set(representative["workloads"]),
            {
                "date_bounded_crawls",
                "document_css_selector",
                "date_bounded_crawl_element_join",
                "bounded_readable_text",
            },
        )
        for workload in representative["workloads"].values():
            self.assertGreaterEqual(workload["rows"], 1)
            self.assertEqual(workload["warm"]["count"], 1)
            self.assertIn("cumulative_rows_scanned", workload["warm_profile"])
            self.assertIn("scan_operators", workload["warm_profile"])

    def test_rejects_non_positive_repetition_counts(self) -> None:
        catalogue = MagicMock()
        with self.assertRaisesRegex(ValueError, "query_repetitions"):
            run_hot_path_benchmark(catalogue, query_repetitions=0)


if __name__ == "__main__":
    unittest.main()
