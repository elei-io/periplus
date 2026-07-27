import asyncio
from datetime import UTC, datetime
import io
from pathlib import Path
import tempfile
import unittest

from repository.ingestion.external import (
    EvidenceImportService,
    ExternalHtmlMetadata,
)
from repository.catalogue.schema import (
    ATTEMPTS,
    DOCUMENTS,
    TABLE_COLUMNS,
    VISITS,
)
from repository.catalogue.service import _visit_values
from repository.objects.store import FileObjectStore


class _Queue:
    def __init__(self) -> None:
        self.visits = []
        self.crawls = []

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def enqueue_visit(self, evidence) -> None:
        self.visits.append(evidence)

    async def enqueue_crawl(self, record) -> None:
        self.crawls.append(record)


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
                    requested_url="https://example.com/page",
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
        self.assertEqual(first.disposition, "created")
        self.assertEqual(second.disposition, "deduplicated")
        self.assertEqual(queue.visits[0], queue.visits[1])
        self.assertEqual(queue.crawls[0], queue.crawls[1])
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
        self.assertEqual(queue.crawls[0].kind, "import")
        self.assertIsNone(queue.crawls[0].graph_id)

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
        self.assertEqual(len(queue.crawls), 32)
        self.assertEqual(len({result.visit_id for result in results}), 32)


if __name__ == "__main__":
    unittest.main()
