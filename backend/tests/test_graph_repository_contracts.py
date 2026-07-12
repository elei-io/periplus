from __future__ import annotations

from datetime import UTC, datetime
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from materialization.readiness import readiness_event
from materialization.queue import MaterializationScopeJob
from repository.catalogue.records import (
    CrawlMaterializationFanout,
    CrawlMaterializationFanoutMember,
    CrawlRecord,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, crawl_row, failures):
        self.crawl_row = crawl_row
        self.failures = failures

    def execute(self, sql, _parameters):
        if "FROM" in sql and '"crawls"' in sql:
            return _Result([self.crawl_row])
        return _Result([(value,) for value in self.failures])


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
            query_revision_id=None,
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

    def test_readiness_event_is_deterministic_and_reports_failures(self) -> None:
        crawl_id = uuid4()
        run_id = uuid4()
        request_id = uuid4()
        failed_id = uuid4()
        fanout = CrawlMaterializationFanout(
            crawl_id=crawl_id,
            planning_completed_at=datetime.now(UTC),
            triggered_count=1,
            settled_count=1,
            failed_count=1,
            completed_at=datetime.now(UTC),
        )
        catalogue = SimpleNamespace(
            config=SimpleNamespace(alias="atlas", schema="main"),
            connection=_Connection((run_id, request_id), [failed_id]),
        )
        with patch(
            "materialization.readiness.CrawlMaterializationFanoutStore.get",
            return_value=fanout,
        ):
            first = readiness_event(catalogue, crawl_id)
            second = readiness_event(catalogue, crawl_id)

        assert first is not None and second is not None
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(first.status, "failed")
        self.assertEqual(first.failed_materialization_ids, [failed_id])
        self.assertEqual(first.graph_run_id, run_id)
        self.assertEqual(first.crawl_request_id, request_id)


if __name__ == "__main__":
    unittest.main()
