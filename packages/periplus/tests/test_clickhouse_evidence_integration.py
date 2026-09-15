"""Opt-in checks against the disposable local ClickHouse and control Postgres.

Run with PERIPLUS_TEST_CLICKHOUSE=1 using unittest discovery. This writes fresh
fixture identities; it must never target production infrastructure.
"""
import os
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from periplus.ingestion.storage import EvidenceStore
from periplus.platform.catalogue.exceptions import CatalogueConflictError
from periplus.platform.clickhouse import connect_clickhouse
from test_ingestion_evidence import _visit_evidence


@unittest.skipUnless(os.environ.get("PERIPLUS_TEST_CLICKHOUSE") == "1", "requires disposable ClickHouse and Postgres")
class ClickHouseEvidenceIntegrationTests(unittest.TestCase):
    def test_lost_insert_response_reconciles_the_durable_identity(self):
        from unittest.mock import patch
        from periplus.ingestion.storage import evidence_digest
        from periplus.retention.identities import write_claims, WriteClaimUnavailable

        client = connect_clickhouse()
        self.addCleanup(client.close)
        store = EvidenceStore(client)
        evidence = _visit_evidence()
        original = store._insert

        def lose_response(table, row):
            original(table, row)
            raise ConnectionError("Injected disconnect after server commit")

        with patch.object(store, "_insert", side_effect=lose_response):
            with self.assertRaises(ConnectionError):
                store.record_visit(evidence)
        receipt = store.receipt("visit", evidence.visit.visit_id, evidence_digest(evidence))
        self.assertIsNotNone(receipt)
        self.assertFalse(receipt.created)
        # Unknown writes retain their exclusion until the real ownership deadline.
        # Reconciliation is read-only and does not bypass that exclusion.
        with self.assertRaises(WriteClaimUnavailable):
            with write_claims({"observation": [str(evidence.visit.visit_id)]}, wait_seconds=0):
                self.fail("An uncertain writer's identity must remain protected")
        count = client.query("SELECT count() AS n FROM ingest.visits WHERE visit_id={id:UUID}",
                             parameters={"id": str(evidence.visit.visit_id)})["data"][0]["n"]
        self.assertEqual(int(count), 1)

    def test_concurrent_consumers_publish_one_logical_visit(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        evidence = _visit_evidence()
        barrier = Barrier(2)

        def record():
            client = connect_clickhouse()
            try:
                barrier.wait(timeout=10)
                return EvidenceStore(client).record_visit(evidence)
            finally:
                client.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(record) for _ in range(2)]
            receipts = [future.result(timeout=30) for future in futures]
        self.assertEqual(sorted(receipt.created for receipt in receipts), [False, True])
        self.assertEqual(receipts[0].ingested_at, receipts[1].ingested_at)
        client = connect_clickhouse()
        self.addCleanup(client.close)
        count = client.query("SELECT count() AS n FROM ingest.visits WHERE visit_id={id:UUID}",
                             parameters={"id": str(evidence.visit.visit_id)})["data"][0]["n"]
        self.assertEqual(int(count), 1)

    def test_repository_verifies_raw_bytes_before_clickhouse_commit(self):
        from periplus.ingestion.objects.store import FileObjectStore
        from periplus.ingestion.objects.html import RawHtmlRepository
        from periplus.ingestion.objects.document import ExactDocumentRepository
        from periplus.ingestion.queue import visit_ingestion_job
        from periplus.ingestion.service import RepositoryIngestor

        client = connect_clickhouse()
        self.addCleanup(client.close)
        evidence = _visit_evidence()
        with TemporaryDirectory() as directory:
            objects = FileObjectStore(Path(directory))
            html = RawHtmlRepository(objects)
            stored = html.put("<html><body>Fixture</body></html>", source_url=evidence.visit.requested_url,
                              visit_id=evidence.visit.visit_id, observed_at=evidence.visit.observed_at,
                              content_type="text/html")
            evidence = evidence.model_copy(update={"document": evidence.document.model_copy(update={
                "content_sha256": stored.sha256, "content_bytes": stored.size_bytes,
                "object_key": stored.object_key, "stored_bytes": stored.compressed_size_bytes,
            })})
            ingestor = RepositoryIngestor(html_repository=html,
                document_repository=ExactDocumentRepository(objects), evidence_store=EvidenceStore(client))
            ingestor.validate()
            job = visit_ingestion_job(evidence)
            prepared = ingestor.prepare(job)
            result = ingestor.commit_prepared_batch([prepared])[0]
            self.assertTrue(result.created)
            self.assertEqual(ingestor.reconcile_commit(job).evidence_sha256, result.evidence_sha256)
            wrong = evidence.model_copy(update={"document": evidence.document.model_copy(update={
                "stored_bytes": stored.compressed_size_bytes + 1,
            })})
            with self.assertRaisesRegex(ValueError, "stored size"):
                ingestor.prepare(visit_ingestion_job(wrong))

    def test_exact_replay_conflict_and_binary_nested_roundtrip(self):
        client = connect_clickhouse()
        self.addCleanup(client.close)
        store = EvidenceStore(client)
        evidence = _visit_evidence()
        first = store.record_visit(evidence)
        replay = store.record_visit(evidence)
        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(first.ingested_at, replay.ingested_at)
        changed = evidence.model_copy(update={
            "visit": evidence.visit.model_copy(update={"requested_url": "https://example.com/changed"}),
        })
        with self.assertRaises(CatalogueConflictError):
            store.record_visit(changed)
        rows = client.query(
            "SELECT lower(hex(content_sha256)) AS hash, attempts[1].attempt_id AS attempt, "
            "finished_at FROM ingest.visits WHERE visit_id={id:UUID}",
            parameters={"id": str(first.identity)},
        )["data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hash"], evidence.document.content_sha256)
        self.assertEqual(rows[0]["attempt"], str(evidence.attempts[0].attempt_id))
        self.assertEqual(rows[0]["finished_at"], "2026-01-02 00:00:00.000000")


if __name__ == "__main__":
    unittest.main()
