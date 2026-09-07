import asyncio
from datetime import UTC, datetime
import io
from pathlib import Path
import tempfile
import unittest

from periplus.ingestion.external import (
    EvidenceImportService,
    ExternalHtmlMetadata,
)
from periplus.platform.catalogue.schema import (
    ATTEMPTS,
    DOCUMENTS,
    TABLE_COLUMNS,
    VISITS,
)
from periplus.platform.catalogue.service import _visit_values
from periplus.ingestion.objects.store import FileObjectStore


class _Queue:
    def __init__(self) -> None:
        self.visits = []

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def enqueue_visit(self, evidence) -> None:
        self.visits.append(evidence)



class ExternalIngestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_document_attempt_identity_may_be_absent(self):
        self.assertFalse(TABLE_COLUMNS[ATTEMPTS]["attempt_id"].nullable)
        self.assertTrue(TABLE_COLUMNS[DOCUMENTS]["attempt_id"].nullable)
        self.assertEqual(
            TABLE_COLUMNS[VISITS]["provenance"].data_type,
            'STRUCT(kind VARCHAR, "system" VARCHAR, dataset VARCHAR, '
            "source_record_id VARCHAR)",
        )

    async def test_exact_html_is_stored_before_truthful_evidence_is_enqueued(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = _Queue()
            service = EvidenceImportService(
                queue=queue,
                object_store=FileObjectStore(Path(directory)),
            )
            await service.start()
            try:
                metadata = ExternalHtmlMetadata(
                    source_record_id="record-1",
                    system="test-archive",
                    dataset="fixture",
                    requested_url=" HTTP://EXAMPLE.COM:80/page#requested ",
                    effective_url=(
                        "HTTPS://WWW.EXAMPLE.COM:443/final?"
                        "b=2&a=1#effective"
                    ),
                    observed_at=datetime(2024, 1, 2, tzinfo=UTC),
                    status_code=200,
                    charset="windows-1252",
                )
                first = await service.ingest_external_html(
                    io.BytesIO(b"<html><body>\x96</body></html>"),
                    metadata,
                )
                second = await service.ingest_external_html(
                    io.BytesIO(b"<html><body>\x96</body></html>"),
                    metadata,
                )
            finally:
                await service.close()

        self.assertEqual(first.visit_id, second.visit_id)
        self.assertNotIn("crawl_id", first.model_dump())
        self.assertEqual(first.disposition, "created")
        self.assertEqual(second.disposition, "deduplicated")
        self.assertEqual(queue.visits[0], queue.visits[1])
        evidence = queue.visits[0]
        self.assertEqual(evidence.attempts, ())
        self.assertIsNone(evidence.document.attempt_id)
        self.assertEqual(evidence.visit.provenance.kind, "external")
        self.assertEqual(
            _visit_values(evidence.visit)["provenance"],
            {
                "kind": "external",
                "system": "test-archive",
                "dataset": "fixture",
                "source_record_id": "record-1",
            },
        )
        self.assertEqual(evidence.visit.observed_at, metadata.observed_at)
        self.assertIsNone(evidence.visit.started_at)
        self.assertEqual(
            evidence.visit.requested_url,
            "http://example.com/page",
        )
        self.assertEqual(
            evidence.visit.effective_url,
            "https://www.example.com/final?b=2&a=1",
        )

    async def test_source_identity_tuple_is_unambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = _Queue()
            service = EvidenceImportService(queue=queue, object_store=FileObjectStore(Path(directory)))
            await service.start()
            try:
                identities = []
                for system, dataset, record in [('a\nb', 'c', 'd'), ('a', 'b\nc', 'd'),
                                                 ('a', None, 'd'), ('a', 'none', 'd')]:
                    result = await service.ingest_external_html(io.BytesIO(b'<html></html>'),
                        ExternalHtmlMetadata(system=system, dataset=dataset, source_record_id=record,
                            requested_url='https://example.com/', observed_at=datetime(2024,1,2,tzinfo=UTC)))
                    identities.append(result.visit_id)
                self.assertEqual(len(set(identities)), 4)
                self.assertEqual(len(queue.visits), 4)
            finally:
                await service.close()

    async def test_independent_records_can_ingest_concurrently(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = _Queue()
            service = EvidenceImportService(
                queue=queue,
                object_store=FileObjectStore(Path(directory)),
            )
            await service.start()
            try:
                results = await asyncio.gather(
                    *(
                        service.ingest_external_html(
                            io.BytesIO(
                                f"<html><title>{index}</title></html>".encode()
                            ),
                            ExternalHtmlMetadata(
                                source_record_id=f"record-{index}",
                                system="bulk-fixture",
                                requested_url=f"https://example.com/{index}",
                                observed_at=datetime(2024, 1, 2, tzinfo=UTC),
                            ),
                        )
                        for index in range(32)
                    )
                )
            finally:
                await service.close()

        self.assertEqual(len(results), 32)
        self.assertEqual(len(queue.visits), 32)
        self.assertEqual(len({result.visit_id for result in results}), 32)


if __name__ == "__main__":
    unittest.main()
