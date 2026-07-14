from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from ducklake_client import DiskStorage, DuckDBCatalog

from api.routers.graph_runs import _policy_pressure_rows, _run_stage_counts
from repository.catalogue import Catalogue, CatalogueConfig
from runtime.graph_progress import NodeProgress, node_progress_key


class GraphRunMetricsTests(unittest.TestCase):
    def test_policy_pressure_is_the_peak_concurrency_in_each_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            ) as catalogue:
                catalogue.bootstrap()
                catalogue.connection.execute(
                    """
                    INSERT INTO atlas.main.crawls (
                        crawl_id, document_id, graph_id, graph_run_id,
                        graph_node_id, crawl_request_id, purpose, requested_url,
                        normalized_url, page_url, url_scheme, url_host, url_port,
                        url_registrable_domain, url_path, url_query, captured_at,
                        duration_ms, profile, crawl_profile_slug, remote_concurrency,
                        config_hash, config_json, outcome, failure_code, failure_stage,
                        failure_retryable, failure_detail
                    ) VALUES
                        (uuid(), NULL, uuid(), uuid(), uuid(), uuid(), 'use',
                         'https://a.test/1', 'https://a.test/1', 'https://a.test/1',
                         'https', 'a.test', 443, 'a.test', '/1', '',
                         TIMESTAMPTZ '2026-07-14 00:03:00+00', 120000,
                         'http', 'direct', 2, repeat('a', 64), '{}', 'failed',
                         'expected_failure', 'request', false, 'failed'),
                        (uuid(), NULL, uuid(), uuid(), uuid(), uuid(), 'use',
                         'https://a.test/2', 'https://a.test/2', 'https://a.test/2',
                         'https', 'a.test', 443, 'a.test', '/2', '',
                         TIMESTAMPTZ '2026-07-14 00:07:00+00', 300000,
                         'http', 'direct', 2, repeat('b', 64), '{}', 'failed',
                         'expected_failure', 'request', false, 'failed'),
                        (uuid(), NULL, uuid(), uuid(), uuid(), uuid(), 'use',
                         'https://a.test/3', 'https://a.test/3', 'https://a.test/3',
                         'https', 'a.test', 443, 'a.test', '/3', '',
                         TIMESTAMPTZ '2026-07-14 00:13:00+00', 60000,
                         'http', 'direct', 2, repeat('c', 64), '{}', 'failed',
                         'expected_failure', 'request', false, 'failed')
                    """
                )

                rows = _policy_pressure_rows(
                    catalogue,
                    range_start=datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
                    range_end=datetime(2026, 7, 14, 0, 20, tzinfo=UTC),
                    bucket_seconds=300,
                )

                leading_rows = _policy_pressure_rows(
                    catalogue,
                    range_start=datetime(2026, 7, 13, 23, 55, tzinfo=UTC),
                    range_end=datetime(2026, 7, 14, 0, 5, tzinfo=UTC),
                    bucket_seconds=300,
                )

        self.assertEqual([int(row[2]) for row in rows], [2, 1, 1, 0])
        self.assertEqual([int(row[3]) for row in rows], [2, 2, 2, 2])
        self.assertEqual(
            [(int(row[2]), int(row[3])) for row in leading_rows],
            [(0, 2), (2, 2)],
        )


class GraphRunStageMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_requests_are_split_into_fetch_and_downstream_work(self) -> None:
        run_id = uuid4()
        node_id = uuid4()
        progress = NodeProgress(
            graph_run_id=run_id,
            node_id=node_id,
            admitted=10,
            queued=3,
            crawling=2,
            awaiting_navigation=4,
            evaluating_edges=1,
            completed=0,
            failed=0,
            cancelled=0,
            activity=(),
            settled=False,
        )

        class ProgressBucket:
            async def get(self, key: str):
                self_key = node_progress_key(run_id, node_id)
                if key != self_key:
                    raise KeyError(key)
                return SimpleNamespace(value=progress.model_dump_json().encode())

        run = SimpleNamespace(
            id=run_id,
            pending_request_count=10,
            snapshot=SimpleNamespace(nodes=[SimpleNamespace(id=node_id)]),
        )

        counts = await _run_stage_counts(ProgressBucket(), run)

        self.assertEqual(counts, (3, 2, 5))


if __name__ == "__main__":
    unittest.main()
