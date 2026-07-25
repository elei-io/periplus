from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import unittest
from uuid import uuid4

from runtime.catalogue_events import CatalogueDMLTick, dml_subject
from repository.catalogue.records import CrawlRecord, NormalizedUrl


class GraphRepositoryContractTests(unittest.TestCase):
    def test_crawl_record_uses_only_graph_provenance(self) -> None:
        identifiers = [uuid4() for _ in range(6)]
        now = datetime.now(UTC)
        url = NormalizedUrl.from_normalized_url("https://example.com/")
        crawl = CrawlRecord(
            crawl_id=identifiers[0],
            document_id=None,
            graph_id=identifiers[1],
            graph_run_id=identifiers[2],
            graph_node_id=identifiers[3],
            source_edge_id=identifiers[5],
            requested_url=url.normalized_url,
            url=url.normalized_url,
            scheme=url.scheme,
            host=url.host,
            port=url.port,
            registrable_domain=url.registrable_domain,
            path=url.path,
            query=url.query,
            started_at=now,
            completed_at=now,
            policy_schema_version=1,
            effective_policy={},
            effective_policy_hash=hashlib.sha256(b"{}").hexdigest(),
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

    def test_table_tick_is_a_first_class_materialization_trigger(self) -> None:
        table_uuid = uuid4()
        tick = CatalogueDMLTick(
            table_id=12,
            table_uuid=table_uuid,
            schema_name="main",
            table_name="crawls",
            snapshot_id=42,
            snapshot_time=datetime.now(UTC),
            schema_version=3,
        )
        self.assertEqual(dml_subject(table_uuid), f"atlas.catalogue.dml.{table_uuid.hex}")
        self.assertEqual(tick.message_id, f"dml:{table_uuid}:42")

if __name__ == "__main__":
    unittest.main()
