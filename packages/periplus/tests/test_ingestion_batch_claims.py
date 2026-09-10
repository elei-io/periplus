"""Contention never blocks unrelated ingestion jobs or acknowledges uncommitted work."""
import asyncio
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from test_operation_leases import FakeBucket
from test_ingestion_receipts import Results
from periplus.ingestion.consumer import PreparedBatch, _claim_batch, _process_messages
from periplus.ingestion.queue import lineage_ingestion_job, get_ingestion_state
from periplus.platform.catalogue.lineage import CollectionDefinition
from periplus.platform.catalogue.records import IngestionWriteResult
from periplus.platform.messaging.leases import operation_leases, operation_lease_key, OperationLease


def job(collection=None):
    identity = collection or uuid4()
    return lineage_ingestion_job(CollectionDefinition(
        record_id=identity, collection_id=identity, recorded_at=datetime.now(UTC), specification={},
    ))


def message(work):
    return SimpleNamespace(data=work.model_dump_json().encode(), ack=AsyncMock(), nak=AsyncMock(),
                           term=AsyncMock(), in_progress=AsyncMock())


class BatchClaimTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_job_does_not_block_free_jobs_and_expiry_allows_recovery(self):
        busy, free = job(), job()
        messages = [message(busy), message(free)]
        batch = PreparedBatch(messages, [busy, free], [object(), object()])
        bucket = FakeBucket()
        async with operation_leases(bucket, [f"collection:{busy.lineage.collection_id}"], phase="ingestion"):
            async with AsyncExitStack() as claims:
                selected = await _claim_batch(batch, bucket, claims, MagicMock())
                self.assertEqual(selected.jobs, [free])
                messages[0].nak.assert_awaited_once()
                messages[1].nak.assert_not_awaited()
                messages[0].ack.assert_not_awaited()
        self.assertFalse(bucket.values)
        async with AsyncExitStack() as claims:
            self.assertEqual((await _claim_batch(batch, bucket, claims, MagicMock())).jobs, [busy, free])

    async def test_duplicate_deliveries_commit_once_without_early_ack(self):
        work = job()
        first, duplicate = message(work), message(work)
        batch = PreparedBatch([first, duplicate], [work, work], [object(), object()])
        async with AsyncExitStack() as claims:
            selected = await _claim_batch(batch, FakeBucket(), claims, MagicMock())
            self.assertEqual(selected.jobs, [work])
            duplicate.nak.assert_awaited_once()
            duplicate.ack.assert_not_awaited()

    async def test_shared_identities_inside_batch_do_not_conflict(self):
        first = job()
        second = first.model_copy(update={"request_id": "another-delivery"})
        batch = PreparedBatch([message(first), message(second)], [first, second], [object(), object()])
        async with AsyncExitStack() as claims:
            selected = await _claim_batch(batch, FakeBucket(), claims, MagicMock())
            self.assertEqual(len(selected.jobs), 2)

    async def process(self, messages, results, ingestor, bucket=None):
        await _process_messages(jetstream=SimpleNamespace(), results_store=results,
            ingestor=ingestor, leases=bucket or FakeBucket(), messages=messages,
            lane=SimpleNamespace(active_operation_count=0), metrics=MagicMock())

    async def test_commit_success_then_ack_failure_replay_skips_write(self):
        work = job(); delivery = message(work); results = Results()
        receipt = IngestionWriteResult(kind="lineage", identity=work.identity, created=True, repository_snapshot=7)
        ingestor = SimpleNamespace(prepare=MagicMock(return_value=object()),
                                  commit_prepared_batch=MagicMock(return_value=[receipt]))
        delivery.ack.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            await self.process([delivery], results, ingestor)
        self.assertEqual((await get_ingestion_state(results, work.request_id)).status, "succeeded")
        replay = message(work)
        await self.process([replay], results, ingestor)
        replay.ack.assert_awaited_once()
        self.assertEqual(ingestor.commit_prepared_batch.call_count, 1)

    async def test_state_outage_retains_delivery(self):
        work = job(); delivery = message(work)
        results = SimpleNamespace(create=AsyncMock(side_effect=TimeoutError()))
        ingestor = SimpleNamespace(prepare=MagicMock(), commit_prepared_batch=MagicMock())
        await self.process([delivery], results, ingestor)
        delivery.nak.assert_awaited_once()
        delivery.term.assert_not_awaited()
        delivery.ack.assert_not_awaited()
        ingestor.commit_prepared_batch.assert_not_called()

    async def test_crashed_workers_expired_lease_is_reclaimed(self):
        work = job(); delivery = message(work); bucket = FakeBucket()
        identity = f"ingestion:{work.request_id}"
        expired = OperationLease(owner="dead-worker", phase="ingestion", operation_id=identity,
            acquired_at=datetime.now(UTC)-timedelta(minutes=2),
            heartbeat_at=datetime.now(UTC)-timedelta(minutes=2),
            expires_at=datetime.now(UTC)-timedelta(minutes=1))
        await bucket.create(operation_lease_key("ingestion", identity), expired.model_dump_json().encode())
        async with AsyncExitStack() as claims:
            selected = await _claim_batch(PreparedBatch([delivery], [work], [object()]), bucket, claims, MagicMock())
            self.assertEqual(selected.jobs, [work])
        self.assertFalse(bucket.values)

    async def test_cancellation_releases_claims_without_acknowledging(self):
        work = job(); delivery = message(work); bucket = FakeBucket(); results = Results()
        ingestor = SimpleNamespace(prepare=MagicMock(return_value=object()))
        with patch("periplus.ingestion.consumer._catalogue_call", AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await self.process([delivery], results, ingestor, bucket)
        delivery.ack.assert_not_awaited()
        delivery.term.assert_not_awaited()
        self.assertFalse(bucket.values)
        self.assertEqual((await get_ingestion_state(results, work.request_id)).status, "pending")
