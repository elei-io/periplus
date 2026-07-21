from __future__ import annotations

from datetime import UTC, datetime
import unittest
from uuid import uuid4

from runtime.catalogue_events import CatalogueDMLTick, dml_subject
from repository.catalogue.records import CrawlRecord, UrlRecord


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
            requested_url_id=UrlRecord.from_normalized_url(
                "https://example.com/"
            ).url_id,
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
