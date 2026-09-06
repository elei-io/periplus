from datetime import UTC, datetime, timedelta
import hashlib
import unittest
from unittest.mock import MagicMock
from uuid import uuid4

from pydantic import ValidationError

from periplus.platform.catalogue import (
    AttemptRecord,
    CatalogueConflictError,
    CrawlRecord,
    DocumentRecord,
    VisitEvidence,
    VisitRecord,
    attempt_id_for,
    document_id_for,
)
from periplus.platform.catalogue.schema import CRAWLS, STEPS
from periplus.platform.catalogue.records import canonical_json
from periplus.platform.catalogue.service import (
    CatalogueService,
    _decode_json_columns,
    _visit_values,
)
from periplus.ingestion.queue import (
    crawl_ingestion_job,
    visit_ingestion_job,
)


class IngestionEvidenceTests(unittest.TestCase):
    def test_json_columns_decode_to_domain_values(self) -> None:
        self.assertEqual(
            _decode_json_columns(
                CRAWLS,
                {"graph_config": '{"edges":[],"nodes":[]}'},
            ),
            {"graph_config": {"edges": [], "nodes": []}},
        )
        self.assertEqual(
            _decode_json_columns(
                STEPS,
                {"parameters": '[1,"x",{"enabled":true}]'},
            ),
            {"parameters": [1, "x", {"enabled": True}]},
        )

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
                object_key="html/sha256/aa/example.html.zst",
                storage_encoding="zstd",
                stored_bytes=8,
            ),
        )

        job = visit_ingestion_job(evidence)

        self.assertEqual(job.identity, visit_id)
        self.assertNotEqual(document_id.hex, evidence.document.content_sha256)
        self.assertEqual(
            _visit_values(evidence.visit)["provenance"],
            {
                "kind": "periplus",
                "system": None,
                "dataset": None,
                "source_record_id": None,
            },
        )

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
                object_key="documents/sha256/bb/value",
                storage_encoding="identity",
                stored_bytes=1,
            )


class AppendOnlyIngestionServiceTests(unittest.TestCase):
    def test_identical_visit_redelivery_is_a_noop(self) -> None:
        evidence = _visit_evidence()
        catalogue = MagicMock()
        catalogue.transaction.return_value.__enter__.return_value = catalogue
        catalogue.latest_snapshot.return_value = 42
        service = CatalogueService(catalogue)
        service.get_visit_evidence = MagicMock(
            return_value={evidence.visit.visit_id: evidence}
        )

        result = service.record_visits([evidence])

        self.assertFalse(result[0].created)
        self.assertEqual(result[0].repository_snapshot, 42)
        catalogue.append_arrow.assert_not_called()

    def test_conflicting_visit_redelivery_fails_without_writes(self) -> None:
        evidence = _visit_evidence()
        conflicting = evidence.model_copy(
            update={
                "visit": evidence.visit.model_copy(
                    update={"status_code": 201}
                )
            }
        )
        catalogue = MagicMock()
        catalogue.transaction.return_value.__enter__.return_value = catalogue
        service = CatalogueService(catalogue)
        service.get_visit_evidence = MagicMock(
            return_value={evidence.visit.visit_id: conflicting}
        )

        with self.assertRaisesRegex(
            CatalogueConflictError,
            "different durable evidence",
        ):
            service.record_visits([evidence])

        catalogue.append_arrow.assert_not_called()

    def test_conflicting_identity_inside_one_batch_fails_before_writes(
        self,
    ) -> None:
        evidence = _visit_evidence()
        conflicting = evidence.model_copy(
            update={
                "visit": evidence.visit.model_copy(
                    update={"outcome": "failed"}
                )
            }
        )
        catalogue = MagicMock()
        service = CatalogueService(catalogue)

        with self.assertRaisesRegex(
            CatalogueConflictError,
            "batch contains conflicting evidence",
        ):
            service.record_visits([evidence, conflicting])

        catalogue.transaction.assert_not_called()
        catalogue.append_arrow.assert_not_called()


def _visit_evidence() -> VisitEvidence:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    visit_id = uuid4()
    document_id = document_id_for(visit_id)
    attempt_id = attempt_id_for(visit_id, 0)
    return VisitEvidence(
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
            detected_media_type="text/html",
            content_sha256="a" * 64,
            content_bytes=10,
            object_key="html/sha256/aa/example.html.zst",
            storage_encoding="zstd",
            stored_bytes=8,
        ),
    )


if __name__ == "__main__":
    unittest.main()
