from __future__ import annotations

from datetime import UTC, datetime
import unittest
from uuid import uuid4

from materialization.queue import MaterializationScopeJob
from repository.catalogue.records import CrawlRecord


class GraphRepositoryContractTests(unittest.TestCase):
    def test_crawl_record_uses_only_graph_provenance(self) -> None:
        identifiers = [uuid4() for _ in range(6)]
        crawl = CrawlRecord(
            crawl_id=identifiers[0],
            document_id=None,
            graph_id=identifiers[1],
            graph_run_id=identifiers[2],
            graph_node_id=identifiers[3],
            crawl_request_id=identifiers[4],
            source_edge_id=identifiers[5],
            requested_url="https://example.com",
            normalized_url="https://example.com/",
            captured_at=datetime.now(UTC),
            policy_config_json={},
            policy_config_hash="a" * 64,
            outcome="failed",
            failure_code="expected_failure",
            failure_stage="request",
            failure_retryable=False,
            failure_detail="failed",
        )

        dumped = crawl.model_dump()
        self.assertEqual(dumped["graph_run_id"], identifiers[2])
        self.assertNotIn("task_id", dumped)
        self.assertNotIn("task_revision", dumped)
        self.assertNotIn("primitive", dumped)

    def test_crawl_scope_is_a_first_class_materialization_job(self) -> None:
        crawl_id = uuid4()
        job = MaterializationScopeJob(
            materialization_id=uuid4(),
            definition_revision_id=uuid4(),
            target_table="page_links",
            scope_kind="crawl",
            scope_column="crawl_id",
            scope_id=str(crawl_id),
            operation_id="operation",
            source="live",
            enqueued_at=datetime.now(UTC),
        )
        self.assertEqual(job.scope_kind, "crawl")
        self.assertEqual(job.scope_id, str(crawl_id))

if __name__ == "__main__":
    unittest.main()
