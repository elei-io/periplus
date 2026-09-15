"""Import recovery and control boundaries without live provider dependencies."""
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4
import unittest
from unittest.mock import AsyncMock, Mock

from pydantic import ValidationError
from operational_state_fixture import operational_state
from test_common_crawl import fixture
from periplus.ingestion.archive import archived_jobs
from periplus.ingestion.imports.control import ImportConflict, ImportControl
from periplus.ingestion.imports.models import ImportRecord
from periplus.ingestion.imports.schemas import ImportSpec
from periplus.ingestion.imports.worker import ImportWorker
from periplus.ingestion.objects.store import FileObjectStore
from periplus.crawl.control.collections.schemas import CollectionSpec


class ImportTests(unittest.TestCase):
    def setUp(self):
        sessions = operational_state(self)
        ImportRecord.__table__.create(sessions.kw["bind"])
        self.control = ImportControl(sessions)
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = FileObjectStore(Path(directory.name))
        self.item, self.data, self.body = fixture()
        self.remote = Mock()
        self.remote.lookup.return_value = self.item
        self.remote.fetch.return_value = self.data
        self.queue = SimpleNamespace(reconcile=AsyncMock(return_value=SimpleNamespace(status="pending")))
        self.worker = ImportWorker(self.control, self.remote, self.store, self.queue)
        self.spec = ImportSpec(dataset=self.item.dataset, urls=(self.item.url,),
            captured_from=datetime(2026, 8, 1, tzinfo=UTC), captured_until=datetime(2026, 9, 1, tzinfo=UTC))

    def create(self, **changes):
        return self.control.create(uuid4(), self.spec.model_copy(update=changes))

    def step(self, identity):
        asyncio.run(self.worker.step(self.control.get(identity)))
        return self.control.get(identity)

    def test_job_checkpoints_archive_before_delivery_and_resumes_without_refetch(self):
        job = self.create()
        selected = self.step(job.id)
        self.assertEqual(selected.progress.current, self.item)
        archived = self.step(job.id)
        self.assertIsNotNone(archived.progress.archive_key)
        self.assertEqual(self.queue.reconcile.await_count, 0)
        self.assertEqual(len(tuple(archived_jobs(self.store))), 1)
        self.worker = ImportWorker(self.control, Mock(), self.store, self.queue)
        done = self.step(job.id)
        self.assertEqual(done.status, "completed")
        self.assertEqual(done.progress.results[0].captured_at, self.item.captured_at)
        self.assertEqual(self.remote.fetch.call_count, 1)
        self.assertEqual(self.queue.reconcile.await_count, 1)

    def test_publish_failure_blocks_and_retry_uses_same_archived_capture(self):
        job = self.create()
        self.step(job.id)
        self.step(job.id)
        self.queue.reconcile.side_effect = RuntimeError("offline")
        self.assertEqual(self.step(job.id).status, "blocked")
        self.queue.reconcile.side_effect = None
        self.control.action(job.id, "retry")
        self.assertEqual(self.step(job.id).status, "completed")
        self.assertEqual(self.remote.fetch.call_count, 1)
        identities = [call.args[0].identity for call in self.queue.reconcile.await_args_list]
        self.assertEqual(len(set(identities)), 1)

    def test_missing_and_unsupported_do_not_publish_or_create_raw_evidence(self):
        self.remote.lookup.return_value = None
        missing = self.create()
        self.assertEqual(self.step(missing.id).progress.results[0].status, "missing")
        item, data, _ = fixture(extra_warc={"WARC-Truncated": "length"})
        self.remote.lookup.return_value, self.remote.fetch.return_value = item, data
        unsupported = self.create()
        self.step(unsupported.id)
        result = self.step(unsupported.id)
        self.assertEqual(result.progress.results[0].status, "unsupported")
        self.assertFalse(tuple(archived_jobs(self.store)))
        self.queue.reconcile.assert_not_awaited()

    def test_budget_is_reserved_before_fetch_and_limits_failed_retries(self):
        job = self.create(max_download_bytes=len(self.data))
        self.step(job.id)
        self.remote.fetch.side_effect = RuntimeError("interrupted range")
        failed = self.step(job.id)
        self.assertEqual(failed.progress.reserved_download_bytes, len(self.data))
        self.control.action(job.id, "retry")
        blocked = self.step(job.id)
        self.assertIn("budget exhausted", blocked.error)
        self.assertEqual(self.remote.fetch.call_count, 1)

    def test_cancel_fences_stale_progress_and_leaves_archive_replayable(self):
        job = self.create()
        self.step(job.id)
        archived = self.step(job.id)
        cancelled = self.control.action(job.id, "cancel")
        with self.assertRaises(ImportConflict):
            self.control.save(archived, progress=archived.progress, status="completed")
        self.assertEqual(self.step(job.id).status, cancelled.status)
        self.assertEqual(len(tuple(archived_jobs(self.store))), 1)
        self.queue.reconcile.assert_not_awaited()

    def test_reimport_has_same_capture_and_shared_body(self):
        first, second = self.create(), self.create()
        for job in (first, second):
            for _ in range(3):
                self.step(job.id)
        one, two = (self.control.get(job.id).progress.results[0] for job in (first, second))
        self.assertEqual(one.capture_id, two.capture_id)
        self.assertFalse(one.already_archived)
        self.assertTrue(two.already_archived)
        self.assertEqual(len(tuple(archived_jobs(self.store))), 1)

    def test_intent_idempotency_and_removed_collection_contract(self):
        job = self.create()
        self.assertEqual(self.control.create(job.id, self.spec).id, job.id)
        with self.assertRaises(ImportConflict):
            self.control.create(job.id, self.spec.model_copy(update={"max_download_bytes": 1}))
        with self.assertRaises(ValidationError):
            CollectionSpec(crawler="common_crawl")
        with self.assertRaises(ValidationError):
            ImportSpec(**{**self.spec.model_dump(), "urls": ["https://example.com/*"]})
        with self.assertRaises(ValidationError):
            ImportSpec(**{**self.spec.model_dump(), "captured_until": datetime(2020, 1, 1, tzinfo=UTC)})


if __name__ == "__main__":
    unittest.main()
