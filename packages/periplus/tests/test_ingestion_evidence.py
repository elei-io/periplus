from operational_state_fixture import operational_state
from capture_policy_fixture import capture_policy
from datetime import UTC, datetime
import unittest
from unittest.mock import MagicMock
from uuid import uuid4

from pydantic import ValidationError

from periplus.platform.catalogue import (
    AttemptRecord,
    CatalogueConflictError,
    DocumentRecord,
    VisitEvidence,
    VisitRecord,
    attempt_id_for,
    document_id_for,
)
from periplus.platform.catalogue.schema import STEPS
from periplus.platform.catalogue.service import (
    CatalogueService,
    _decode_json_columns,
    _visit_values,
)
from periplus.ingestion.queue import (
    visit_ingestion_job,
)


class IngestionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.sessions = operational_state(self)

    def test_native_policy_is_required(self):
        evidence = _visit_evidence()
        values = evidence.visit.model_dump(mode="json")
        values.pop("capture_policy")
        with self.assertRaisesRegex(ValidationError, "frozen capture_policy"):
            VisitRecord.model_validate(values)

    def test_retired_import_provenance_is_rejected(self):
        values = _visit_evidence().visit.model_dump(mode="json")
        values["provenance"] = {"kind": "external"}
        with self.assertRaises(ValidationError):
            VisitRecord.model_validate(values)

    def test_policy_survives_frozen_ingestion_job(self):
        evidence = _visit_evidence()
        job = visit_ingestion_job(evidence)
        restored = type(job).model_validate_json(job.model_dump_json())
        self.assertEqual(restored, job)
        self.assertIn('"capture_policy"', job.model_dump_json())

    def test_json_columns_decode_to_domain_values(self) -> None:
        self.assertEqual(
            _decode_json_columns(
                STEPS,
                {"parameters": '[1,"x",{"enabled":true}]'},
            ),
            {"parameters": [1, "x", {"enabled": True}]},
        )

    def test_removed_crawl_jobs_and_payload_fields_are_rejected(self):
        from periplus.ingestion.queue import IngestionJob
        from periplus.platform.catalogue.schema import TABLE_COLUMNS
        self.assertNotIn('ingest.crawls', {relation.qualified for relation in TABLE_COLUMNS})
        with self.assertRaises(ValidationError):
            IngestionJob(kind='crawl', request_id='crawl-old', enqueued_at=datetime.now(UTC), crawl={})

    def test_visit_evidence_uses_observation_and_content_identities(self) -> None:
        now = datetime.now(UTC)
        visit_id = uuid4()
        attempt_id = attempt_id_for(visit_id, 0)
        document_id = document_id_for(visit_id)
        evidence = VisitEvidence(
            visit=VisitRecord(
                capture_policy=capture_policy(),
                visit_id=visit_id,
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
        with self.assertRaises(ValidationError):
            type(job).model_validate(job.model_dump() | {"crawl": {}})

        self.assertEqual(job.identity, visit_id)
        self.assertNotEqual(document_id.hex, evidence.document.content_sha256)
        self.assertNotIn("provenance", _visit_values(evidence.visit))

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
    def setUp(self):
        self.sessions = operational_state(self)

    def test_identical_visit_redelivery_is_a_noop(self) -> None:
        evidence = _visit_evidence()
        catalogue = MagicMock()
        catalogue.trusted_connection.execute.return_value.fetchall.side_effect = [
            [(str(evidence.visit.visit_id), None)],
            [(evidence.document.content_sha256, None)],
        ]
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
        catalogue.trusted_connection.execute.return_value.fetchall.side_effect = [
            [(str(evidence.visit.visit_id), None)],
            [(evidence.document.content_sha256, None)],
        ]
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
        catalogue.trusted_connection.execute.return_value.fetchall.side_effect = [
            [(str(evidence.visit.visit_id), None)],
            [(evidence.document.content_sha256, None)],
        ]
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
            capture_policy=capture_policy(),
            visit_id=visit_id,
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
