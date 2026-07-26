from datetime import UTC, datetime, timedelta
import hashlib
import unittest
from uuid import uuid4

from pydantic import ValidationError

from repository.catalogue import (
    AttemptRecord,
    CrawlRecord,
    DocumentRecord,
    VisitEvidence,
    VisitRecord,
    attempt_id_for,
    document_id_for,
)
from repository.catalogue.records import canonical_json
from repository.ingestion.queue import (
    crawl_ingestion_job,
    visit_ingestion_job,
)


class IngestionEvidenceTests(unittest.TestCase):
    def test_crawl_job_has_stable_identity_and_canonical_hash(self) -> None:
        now = datetime.now(UTC)
        config = {"nodes": [], "edges": [], "version": 1}
        record = CrawlRecord(
            crawl_id=uuid4(),
            graph_id=uuid4(),
            graph_config_hash=hashlib.sha256(
                canonical_json(config).encode()
            ).hexdigest(),
            graph_config=config,
            root_url_count=2,
            started_at=now,
            finished_at=now + timedelta(seconds=1),
            stop_reason="completed",
        )

        first = crawl_ingestion_job(record)
        second = crawl_ingestion_job(record)

        self.assertEqual(first.request_id, second.request_id)
        self.assertEqual(first.identity, record.crawl_id)

    def test_visit_evidence_uses_observation_and_content_identities(self) -> None:
        now = datetime.now(UTC)
        visit_id = uuid4()
        attempt_id = attempt_id_for(visit_id, 0)
        document_id = document_id_for(visit_id)
        evidence = VisitEvidence(
            visit=VisitRecord(
                visit_id=visit_id,
                crawl_id=uuid4(),
                requested_url="https://example.com/",
                effective_url="https://example.com/",
                admitted_at=now,
                started_at=now,
                observed_at=now,
                finished_at=now,
                outcome="succeeded",
                status_code=200,
                document_id=document_id,
            ),
            attempts=(
                AttemptRecord(
                    attempt_id=attempt_id,
                    visit_id=visit_id,
                    attempt_index=0,
                    started_at=now,
                    finished_at=now,
                    effective_url="https://example.com/",
                    status_code=200,
                    outcome="succeeded",
                ),
            ),
            document=DocumentRecord(
                document_id=document_id,
                visit_id=visit_id,
                attempt_id=attempt_id,
                observed_at=now,
                representation="rendered_html",
                declared_media_type="text/html",
                detected_media_type="text/html",
                charset="utf-8",
                content_sha256="a" * 64,
                content_bytes=10,
                object_key="raw/html/sha256/aa/example.html.zst",
                storage_encoding="zstd",
                stored_bytes=8,
            ),
        )

        job = visit_ingestion_job(evidence)

        self.assertEqual(job.identity, visit_id)
        self.assertNotEqual(document_id.hex, evidence.document.content_sha256)

    def test_document_requires_visit_derived_observation_identity(self) -> None:
        visit_id = uuid4()
        with self.assertRaises(ValidationError):
            DocumentRecord(
                document_id=uuid4(),
                visit_id=visit_id,
                attempt_id=attempt_id_for(visit_id, 0),
                observed_at=datetime.now(UTC),
                representation="response_body",
                detected_media_type="application/pdf",
                content_sha256="b" * 64,
                content_bytes=1,
                object_key="raw/documents/sha256/bb/value",
                storage_encoding="identity",
                stored_bytes=1,
            )


if __name__ == "__main__":
    unittest.main()
