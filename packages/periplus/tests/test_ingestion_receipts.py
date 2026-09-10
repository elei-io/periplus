"""Lost receipt recovery republishes immutable evidence, never acquisition work."""
from datetime import UTC, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from nats.js.errors import KeyNotFoundError, KeyWrongLastSequenceError, NotFoundError

from periplus.ingestion.queue import (
    IngestionQueueClient, lineage_ingestion_job, store_ingestion_response,
)
from periplus.platform.catalogue.lineage import CollectionDefinition
from periplus.platform.catalogue.records import IngestionWriteResult


class Results:
    def __init__(self):
        self.entries = {}
        self.revision = 0

    async def get(self, key):
        if key not in self.entries:
            raise KeyNotFoundError()
        return self.entries[key]

    async def create(self, key, value):
        if key in self.entries:
            raise KeyWrongLastSequenceError()
        self.revision += 1
        self.entries[key] = SimpleNamespace(value=value, revision=self.revision)
        return self.revision

    async def update(self, key, value, last):
        current = await self.get(key)
        if current.revision != last:
            raise KeyWrongLastSequenceError()
        self.revision += 1
        self.entries[key] = SimpleNamespace(value=value, revision=self.revision)
        return self.revision


class IngestionReceiptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = IngestionQueueClient()
        self.client.client = object()
        self.client.jetstream = SimpleNamespace(
            publish=AsyncMock(return_value=SimpleNamespace(seq=12)),
            get_msg=AsyncMock(), add_consumer=AsyncMock(),
        )
        self.client.results = Results()
        identity = uuid4()
        self.job = lineage_ingestion_job(CollectionDefinition(
            record_id=identity, collection_id=identity,
            recorded_at=datetime.now(UTC), specification={"page_limit": 1},
        ))

    async def test_expired_receipt_replays_same_job_and_reconciles_later_snapshot(self):
        pending = await self.client.reconcile(self.job)
        self.assertEqual(pending.status, "pending")
        result = IngestionWriteResult(kind="lineage", identity=self.job.identity,
                                      created=True, repository_snapshot=3)
        await store_ingestion_response(self.client.results, job=self.job, result=result)
        committed = await self.client.reconcile(self.job)
        self.assertEqual(committed.result, result)
        self.assertEqual(self.client.jetstream.publish.await_count, 1)

        self.client.results.entries.clear()
        recovered = await self.client.reconcile(self.job)
        self.assertEqual(recovered.status, "pending")
        self.assertIsNone(recovered.result)
        calls = self.client.jetstream.publish.await_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        replay = result.model_copy(update={"created": False, "repository_snapshot": 9})
        await store_ingestion_response(self.client.results, job=self.job, result=replay)
        self.assertEqual((await self.client.reconcile(self.job)).result.repository_snapshot, 9)
        self.client.jetstream.add_consumer.assert_not_awaited()

    async def test_failed_or_conflicting_evidence_is_not_automatically_requeued(self):
        await self.client.reconcile(self.job)
        await store_ingestion_response(self.client.results, job=self.job, error="invalid evidence")
        self.assertEqual((await self.client.reconcile(self.job)).status, "failed")
        self.assertEqual(self.client.jetstream.publish.await_count, 1)
        changed = self.job.model_copy(update={"lineage": self.job.lineage.model_copy(
            update={"specification": {"page_limit": 2}},
        )})
        with self.assertRaisesRegex(RuntimeError, "different evidence"):
            await self.client.reconcile(changed)
        self.assertEqual(self.client.jetstream.publish.await_count, 1)

    async def test_existing_delivery_is_not_republished_even_after_long_wait(self):
        await self.client.reconcile(self.job)
        self.client.jetstream.get_msg.return_value = SimpleNamespace(data=self.job.model_dump_json().encode())
        for _ in range(20):
            await self.client.reconcile(self.job)
        self.assertEqual(self.client.jetstream.publish.await_count, 1)

    async def test_missing_delivery_is_republished_and_sequence_updated(self):
        await self.client.reconcile(self.job)
        self.client.jetstream.get_msg.side_effect = NotFoundError()
        self.client.jetstream.publish.return_value = SimpleNamespace(seq=15)
        recovered = await self.client.reconcile(self.job)
        self.assertEqual(recovered.published_sequence, 15)
        self.assertEqual(self.client.jetstream.publish.await_count, 2)

    async def test_sequence_reused_after_restore_does_not_hide_missing_delivery(self):
        await self.client.reconcile(self.job)
        self.client.jetstream.get_msg.return_value = SimpleNamespace(data=b"different job")
        self.client.jetstream.publish.return_value = SimpleNamespace(seq=20)
        self.assertEqual((await self.client.reconcile(self.job)).published_sequence, 20)

    async def test_unavailable_stream_does_not_trigger_speculative_publish(self):
        await self.client.reconcile(self.job)
        self.client.jetstream.get_msg.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            await self.client.reconcile(self.job)
        self.assertEqual(self.client.jetstream.publish.await_count, 1)

    async def test_publish_success_then_receipt_write_failure_recovers(self):
        original = self.client.results.update
        self.client.results.update = AsyncMock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            await self.client.reconcile(self.job)
        self.client.results.update = original
        # JetStream's duplicate acknowledgement points to the original delivery.
        recovered = await self.client.reconcile(self.job)
        self.assertEqual(recovered.published_sequence, 12)
        self.client.jetstream.get_msg.return_value = SimpleNamespace(data=self.job.model_dump_json().encode())
        await self.client.reconcile(self.job)
        self.assertEqual(self.client.jetstream.publish.await_count, 2)
