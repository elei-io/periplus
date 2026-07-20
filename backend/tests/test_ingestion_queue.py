from datetime import UTC, datetime
from types import SimpleNamespace
import unittest
from uuid import uuid4

from repository.catalogue.records import CrawlRecord, UrlRecord
from repository.ingestion.queue import (
    IngestionState,
    record_ingestion_processing_failure,
)


class IngestionQueueTests(unittest.TestCase):
    def test_ingestion_state_has_resolved_annotations(self) -> None:
        IngestionState.model_rebuild()
        self.assertTrue(IngestionState.__pydantic_complete__)


class IngestionAttemptTests(unittest.IsolatedAsyncioTestCase):
    async def test_processing_failures_ignore_jetstream_delivery_count(self) -> None:
        crawl = CrawlRecord(
            crawl_id=uuid4(),
            document_id=None,
            graph_id=uuid4(),
            graph_run_id=uuid4(),
            graph_node_id=uuid4(),
            crawl_request_id=uuid4(),
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
        state = IngestionState(
            request_id="request",
            status="pending",
            crawl=crawl,
            urls=(),
            crawl_attempts=(),
            enqueued_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        class Bucket:
            revision = 1
            value = state.model_dump_json().encode()

            async def get(self, _key):
                return SimpleNamespace(revision=self.revision, value=self.value)

            async def update(self, _key, value, *, last):
                self.assert_revision(last)
                self.revision += 1
                self.value = value

            def assert_revision(self, last):
                if last != self.revision:
                    raise AssertionError("unexpected revision")

        bucket = Bucket()
        self.assertEqual(
            await record_ingestion_processing_failure(bucket, "request"), 1
        )
        self.assertEqual(
            await record_ingestion_processing_failure(bucket, "request"), 2
        )


if __name__ == "__main__":
    unittest.main()
