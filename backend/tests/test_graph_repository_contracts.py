from __future__ import annotations

from datetime import UTC, datetime
import unittest
from uuid import uuid4

from materialization.queue import MaterializationScopeJob
from repository.catalogue.records import (
    CrawlMaterializationFanout,
    CrawlMaterializationFanoutMember,
    CrawlRecord,
)


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
            input_json={},
            input_hash="input-hash",
            errors_json=["failed"],
        )

        dumped = crawl.model_dump()
        self.assertEqual(dumped["graph_run_id"], identifiers[2])
        self.assertNotIn("task_id", dumped)
        self.assertNotIn("task_revision", dumped)
        self.assertNotIn("primitive", dumped)

    def test_completed_fanout_requires_every_job_to_settle(self) -> None:
        with self.assertRaisesRegex(ValueError, "settled all triggered work"):
            CrawlMaterializationFanout(
                crawl_id=uuid4(),
                planning_completed_at=datetime.now(UTC),
                triggered_count=2,
                settled_count=1,
                failed_count=0,
                completed_at=datetime.now(UTC),
            )

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
        member = CrawlMaterializationFanoutMember(
            crawl_id=crawl_id,
            materialization_id=job.materialization_id,
            definition_revision_id=job.definition_revision_id,
            scope_kind="crawl",
            scope_id=job.scope_id,
        )

        self.assertEqual(job.scope_kind, "crawl")
        self.assertEqual(member.status, "planned")

if __name__ == "__main__":
    unittest.main()
