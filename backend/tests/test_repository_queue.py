from __future__ import annotations

import asyncio
import json
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from nats.js.errors import KeyNotFoundError, KeyWrongLastSequenceError
from nats.js.api import RetentionPolicy, StorageType

from repository.ducklake import (
    CatalogueWriteResult,
    CrawlRecord,
    RunCrawlUsageRecord,
    RunManifestRecord,
    RunManifestWriteResult,
)
from repository.queue import (
    IngestionJob,
    IngestionQueueClient,
    IngestionState,
    DeadLetterEntry,
    _validate_ingestion_results,
    crawl_ingestion_request_id,
    encode_dead_letter,
    ensure_pending_ingestion,
    ensure_repository_stream,
    decode_run_manifest,
    encode_run_manifest,
    mark_ingestion_published,
    publish_dead_letter,
    requeue_dead_letter,
    run_usage_ingestion_request_id,
    store_ingestion_response,
)


def _crawl(*, captured_at: datetime | None = None) -> CrawlRecord:
    return CrawlRecord(
        crawl_id=UUID(int=1),
        document_id="sha256:" + "a" * 64,
        run_id=UUID(int=2),
        task_id=UUID(int=3),
        task_revision=1,
        primitive="crawl",
        requested_url="https://example.com",
        normalized_url="https://example.com",
        final_url="https://example.com",
        captured_at=captured_at or datetime(2026, 7, 11, tzinfo=UTC),
        status_code=200,
        duration_ms=1,
        input_json={},
        input_hash="input:v1",
    )


def _result() -> CatalogueWriteResult:
    return CatalogueWriteResult(
        document_id="sha256:" + "a" * 64,
        crawl_id=UUID(int=1),
        document_created=True,
        crawl_created=True,
        repository_snapshot=2,
    )


def _manifest() -> RunManifestRecord:
    return RunManifestRecord(
        run_id=UUID(int=4),
        task_id=UUID(int=5),
        task_revision=1,
        primitive="index",
        input_json={"url": "https://example.com"},
        queued_at=datetime(2026, 7, 11, tzinfo=UTC),
    )


def _usage() -> RunCrawlUsageRecord:
    return RunCrawlUsageRecord(
        usage_id=UUID(int=6),
        run_id=UUID(int=4),
        crawl_id=UUID(int=1),
        document_id="sha256:" + "a" * 64,
        requested_url="https://example.com",
        normalized_url="https://example.com",
        source="repository",
        role="primitive_result",
        ordinal=0,
        returned=True,
    )


class _FakeKv:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.revisions: dict[str, int] = {}

    async def get(self, key):
        if key not in self.values:
            raise KeyNotFoundError()
        return SimpleNamespace(value=self.values[key], revision=self.revisions[key])

    async def create(self, key, value):
        if key in self.values:
            raise KeyWrongLastSequenceError()
        self.values[key] = value
        self.revisions[key] = 1
        return 1

    async def put(self, key, value):
        self.values[key] = value
        self.revisions[key] = self.revisions.get(key, 0) + 1
        return self.revisions[key]

    async def update(self, key, value, *, last):
        if self.revisions.get(key) != last:
            raise KeyWrongLastSequenceError()
        return await self.put(key, value)


class _LostInboxSubscription:
    async def next_msg(self, *, timeout):
        await asyncio.sleep(timeout)
        raise asyncio.TimeoutError

    async def unsubscribe(self):
        pass


class _FakeClient:
    def __init__(self) -> None:
        self.subscription = _LostInboxSubscription()

    def new_inbox(self):
        return "_INBOX.test"

    async def subscribe(self, _subject):
        return self.subscription

    async def drain(self):
        pass


class _CompletingJetStream:
    def __init__(self, values: _FakeKv) -> None:
        self.values = values
        self.jobs = []

    async def publish(self, _subject, payload, **_kwargs):
        job = json.loads(payload)
        self.jobs.append(job)
        if job.get("kind") == "manifest":
            manifest = decode_run_manifest(job["run_manifest_zstd"])
            result = RunManifestWriteResult(
                run_id=manifest.run_id,
                manifest_created=True,
                repository_snapshot=1,
            )
            crawl = None
        else:
            result = _result()
            crawl = CrawlRecord.model_validate(job["crawl"])
        state = IngestionState(
            request_id=job["request_id"],
            status="succeeded",
            kind=job.get("kind", "crawl"),
            crawl=crawl,
            run_manifest_zstd=job.get("run_manifest_zstd"),
            run_usage=RunCrawlUsageRecord.model_validate(job["run_usage"])
            if job.get("run_usage") is not None
            else None,
            enqueued_at=datetime.fromisoformat(job["enqueued_at"]),
            updated_at=datetime.now(UTC),
            result=result,
        )
        self.values.values[job["request_id"]] = state.model_dump_json().encode()
        self.values.revisions[job["request_id"]] = (
            self.values.revisions.get(job["request_id"], 0) + 1
        )
        return SimpleNamespace()


class RepositoryQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_dead_letters_use_distinct_deduplication_ids(self) -> None:
        jetstream = AsyncMock()
        job = IngestionJob(
            request_id="crawl-1",
            reply_subject="_INBOX.test",
            enqueued_at=datetime.now(UTC),
            crawl=_crawl(),
        )

        await publish_dead_letter(
            jetstream, job=job, error="first", delivery_count=5
        )
        requeued_job = job.model_copy(
            update={"enqueued_at": job.enqueued_at + timedelta(seconds=1)}
        )
        await publish_dead_letter(
            jetstream, job=requeued_job, error="second", delivery_count=5
        )

        first = jetstream.publish.await_args_list[0].kwargs["headers"]["Nats-Msg-Id"]
        second = jetstream.publish.await_args_list[1].kwargs["headers"]["Nats-Msg-Id"]
        self.assertNotEqual(first, second)

    async def test_repository_stream_rejects_memory_storage(self) -> None:
        config = SimpleNamespace(
            subjects=["atlas.repository.ingest"],
            retention=RetentionPolicy.WORK_QUEUE,
            storage=StorageType.MEMORY,
            num_replicas=1,
        )
        jetstream = SimpleNamespace(
            stream_info=AsyncMock(
                return_value=SimpleNamespace(config=config)
            )
        )

        with self.assertRaisesRegex(RuntimeError, "file storage"):
            await ensure_repository_stream(jetstream)

    async def test_result_bucket_rejects_memory_storage(self) -> None:
        config = SimpleNamespace(
            storage=StorageType.MEMORY,
            max_msgs_per_subject=1,
            max_age=7 * 24 * 60 * 60,
            max_bytes=256 * 1024 * 1024,
            num_replicas=1,
        )
        bucket = SimpleNamespace(
            status=AsyncMock(
                return_value=SimpleNamespace(
                    stream_info=SimpleNamespace(config=config)
                )
            )
        )

        with self.assertRaisesRegex(RuntimeError, "file storage"):
            await _validate_ingestion_results(bucket)

    async def test_dead_letter_requeue_resets_state_and_removes_entry(self) -> None:
        values = _FakeKv()
        crawl = _crawl()
        request_id = crawl_ingestion_request_id(crawl.crawl_id)
        pending = await ensure_pending_ingestion(
            values,
            request_id=request_id,
            crawl=crawl,
        )
        failed = pending.model_copy(
            update={
                "status": "failed",
                "updated_at": datetime.now(UTC),
                "error": "catalogue rejected input",
            }
        )
        values.values[request_id] = failed.model_dump_json().encode()
        job = IngestionJob(
            request_id=request_id,
            reply_subject="_INBOX.test",
            enqueued_at=pending.enqueued_at,
            crawl=crawl,
        )
        dead_letter = DeadLetterEntry(
            job=job,
            error=failed.error or "failed",
            failed_at=datetime.now(UTC),
            delivery_count=5,
        )

        class _JetStream:
            published = []
            deleted = []

            async def get_msg(self, _stream, *, seq):
                self.requested = seq
                return SimpleNamespace(data=encode_dead_letter(dead_letter))

            async def publish(self, subject, payload, **kwargs):
                self.published.append((subject, payload, kwargs))

            async def delete_msg(self, _stream, sequence):
                self.deleted.append(sequence)
                return True

        jetstream = _JetStream()
        observed = await requeue_dead_letter(jetstream, values, 9)

        self.assertEqual(observed, dead_letter)
        state = IngestionState.model_validate_json(values.values[request_id])
        self.assertEqual(state.status, "pending")
        self.assertIsNone(state.error)
        self.assertIsNotNone(state.published_at)
        self.assertEqual(jetstream.deleted, [9])

    async def test_large_manifest_is_compressed_below_default_nats_envelope(self) -> None:
        manifest = _manifest().model_copy(
            update={
                "input_json": {
                    "urls": [
                        f"https://example.com/items/{index:05d}/" + "x" * 140
                        for index in range(10_000)
                    ]
                }
            }
        )
        encoded = encode_run_manifest(manifest)
        state = IngestionState(
            request_id=f"manifest-{manifest.run_id.hex}",
            status="pending",
            kind="manifest",
            run_manifest_zstd=encoded,
            enqueued_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        self.assertGreater(len(manifest.model_dump_json().encode()), 1_000_000)
        self.assertLess(len(state.model_dump_json().encode()), 900 * 1024)
        self.assertEqual(decode_run_manifest(encoded), manifest)

    async def test_oversized_pending_envelope_is_rejected_before_kv_create(self) -> None:
        values = _FakeKv()
        oversized = _crawl().model_copy(update={"input_json": {"value": "x" * 500}})

        with patch.dict("os.environ", {"ATLAS_NATS_MAX_ENVELOPE_BYTES": "100"}):
            with self.assertRaisesRegex(ValueError, "pending-state envelope"):
                await ensure_pending_ingestion(
                    values,
                    request_id=crawl_ingestion_request_id(oversized.crawl_id),
                    crawl=oversized,
                )

        self.assertEqual(values.values, {})

    async def test_cas_loss_to_concurrent_success_returns_success(self) -> None:
        class _ConcurrentSuccessKv(_FakeKv):
            winning_payload: bytes | None = None

            async def update(self, key, value, *, last):
                if self.winning_payload is not None:
                    payload = self.winning_payload
                    self.winning_payload = None
                    await self.put(key, payload)
                    raise KeyWrongLastSequenceError()
                return await super().update(key, value, last=last)

        values = _ConcurrentSuccessKv()
        crawl = _crawl()
        request_id = crawl_ingestion_request_id(crawl.crawl_id)
        pending = await ensure_pending_ingestion(
            values,
            request_id=request_id,
            crawl=crawl,
        )
        job = IngestionJob(
            request_id=request_id,
            reply_subject="_INBOX.test",
            enqueued_at=pending.enqueued_at,
            crawl=crawl,
        )
        winner = pending.model_copy(
            update={
                "status": "succeeded",
                "updated_at": datetime.now(UTC),
                "result": _result(),
            }
        )
        values.winning_payload = winner.model_dump_json().encode()

        observed = await store_ingestion_response(
            values,
            job=job,
            error="late failure",
        )

        self.assertEqual(observed, winner)
        self.assertEqual(
            IngestionState.model_validate_json(values.values[request_id]),
            winner,
        )

    async def test_late_failure_cannot_overwrite_durable_success(self) -> None:
        values = _FakeKv()
        crawl = _crawl()
        request_id = crawl_ingestion_request_id(crawl.crawl_id)
        pending = await ensure_pending_ingestion(
            values,
            request_id=request_id,
            crawl=crawl,
        )
        job = IngestionJob(
            request_id=request_id,
            reply_subject="_INBOX.test",
            enqueued_at=pending.enqueued_at,
            crawl=crawl,
        )

        succeeded = await store_ingestion_response(
            values,
            job=job,
            result=_result(),
        )
        success_revision = values.revisions[request_id]
        observed = await store_ingestion_response(
            values,
            job=job,
            error="late failure",
        )

        self.assertEqual(succeeded.status, "succeeded")
        self.assertEqual(observed, succeeded)
        self.assertEqual(values.revisions[request_id], success_revision)

    async def test_success_repairs_a_previously_recorded_failure(self) -> None:
        values = _FakeKv()
        crawl = _crawl()
        request_id = crawl_ingestion_request_id(crawl.crawl_id)
        pending = await ensure_pending_ingestion(
            values,
            request_id=request_id,
            crawl=crawl,
        )
        job = IngestionJob(
            request_id=request_id,
            reply_subject="_INBOX.test",
            enqueued_at=pending.enqueued_at,
            crawl=crawl,
        )

        failed = await store_ingestion_response(
            values,
            job=job,
            error="too early",
        )
        repaired = await store_ingestion_response(
            values,
            job=job,
            result=_result(),
        )

        self.assertEqual(failed.status, "failed")
        self.assertEqual(repaired.status, "succeeded")
        self.assertEqual(repaired.result, _result())

    async def test_manifest_is_a_separate_operation_from_cache_usage(self) -> None:
        values = _FakeKv()
        client = IngestionQueueClient()
        client.client = _FakeClient()
        client.jetstream = _CompletingJetStream(values)
        client.results = values
        usage = _usage()

        with patch.dict(
            "os.environ",
            {
                "ATLAS_INGEST_RESULT_POLL_SECONDS": "0.001",
            },
        ):
            await client.submit_manifest(_manifest())
            await client.submit(
                _crawl(),
                request_id=run_usage_ingestion_request_id(usage.usage_id),
                run_usage=usage,
            )

        manifest_job, usage_job = client.jetstream.jobs
        self.assertEqual(decode_run_manifest(manifest_job["run_manifest_zstd"]), _manifest())
        self.assertIsNone(usage_job["run_manifest_zstd"])
        self.assertEqual(RunCrawlUsageRecord.model_validate(usage_job["run_usage"]), usage)

    async def test_lost_inbox_notification_is_reconciled_from_durable_state(self) -> None:
        values = _FakeKv()
        client = IngestionQueueClient()
        client.client = _FakeClient()
        client.jetstream = _CompletingJetStream(values)
        client.results = values

        with patch.dict(
            "os.environ",
            {
                "ATLAS_INGEST_RESULT_POLL_SECONDS": "0.001",
            },
        ):
            result = await client.submit(_crawl())

        self.assertEqual(result, _result())
        self.assertEqual(len(client.jetstream.jobs), 1)

    async def test_terminal_state_returns_without_republishing(self) -> None:
        values = _FakeKv()
        crawl = _crawl()
        request_id = crawl_ingestion_request_id(crawl.crawl_id)
        state = IngestionState(
            request_id=request_id,
            status="succeeded",
            crawl=crawl,
            enqueued_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            result=_result(),
        )
        values.values[request_id] = state.model_dump_json().encode()
        values.revisions[request_id] = 1
        jetstream = _CompletingJetStream(values)
        client = IngestionQueueClient()
        client.client = _FakeClient()
        client.jetstream = jetstream
        client.results = values

        result = await client.submit(crawl)

        self.assertEqual(result, _result())
        self.assertEqual(jetstream.jobs, [])

    async def test_pending_state_retains_first_frozen_crawl_on_resume(self) -> None:
        values = _FakeKv()
        original = _crawl()
        later = _crawl(captured_at=datetime(2026, 7, 12, tzinfo=UTC))
        request_id = crawl_ingestion_request_id(original.crawl_id)

        first = await ensure_pending_ingestion(
            values,
            request_id=request_id,
            crawl=original,
        )
        resumed = await ensure_pending_ingestion(
            values,
            request_id=request_id,
            crawl=later,
        )

        self.assertEqual(first.crawl, original)
        self.assertEqual(resumed.crawl, original)

    async def test_puback_marker_is_persisted_with_compare_and_swap(self) -> None:
        values = _FakeKv()
        crawl = _crawl()
        request_id = crawl_ingestion_request_id(crawl.crawl_id)
        await ensure_pending_ingestion(
            values,
            request_id=request_id,
            crawl=crawl,
        )

        published = await mark_ingestion_published(values, request_id)

        self.assertIsNotNone(published.published_at)
        self.assertEqual(values.revisions[request_id], 2)

    async def test_resume_republishes_original_pending_envelope(self) -> None:
        values = _FakeKv()
        original = _crawl()
        request_id = crawl_ingestion_request_id(original.crawl_id)
        await ensure_pending_ingestion(
            values,
            request_id=request_id,
            crawl=original,
        )
        jetstream = _CompletingJetStream(values)
        client = IngestionQueueClient()
        client.client = _FakeClient()
        client.jetstream = jetstream
        client.results = values

        with patch.dict(
            "os.environ",
            {
                "ATLAS_INGEST_RESULT_POLL_SECONDS": "0.001",
            },
        ):
            result = await client.resume(original.crawl_id)

        self.assertEqual(result, _result())
        self.assertEqual(
            CrawlRecord.model_validate(jetstream.jobs[0]["crawl"]),
            original,
        )


if __name__ == "__main__":
    unittest.main()
