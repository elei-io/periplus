"""JetStream work and durable result contracts for immutable ingestion evidence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from periplus.platform.config import get_float, get_int
from periplus.platform.config.performance import (
    INGESTION_ACK_WAIT_SECONDS,
    INGESTION_CONSUMER_MAX_ACK_PENDING,
)
from nats.js.api import AckPolicy, ConsumerConfig, KeyValueConfig, StorageType
from nats.js.errors import (
    BadRequestError,
    BucketNotFoundError,
    KeyDeletedError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
    NotFoundError,
)
from pydantic import BaseModel, ConfigDict, model_validator
import zstandard

from periplus.platform.catalogue import (
    CrawlRecord,
    IngestionWriteResult,
    VisitEvidence,
)
from periplus.platform.messaging.catalogue_queue import (
    DEAD_LETTER_STREAM,
    INGEST_DEAD_LETTER_SUBJECT as DEAD_LETTER_SUBJECT,
    INGEST_SUBJECT as SUBJECT,
    WORK_STREAM as STREAM,
    ensure_catalogue_work_stream,
    ensure_dead_letter_stream as ensure_catalogue_dead_letter_stream,
)
from periplus.platform.messaging.topology import validate_kv_contract
from periplus.platform.messaging.client import connect_nats

DURABLE = "periplus-ingestion"
RESULTS_BUCKET = "periplus_ingestion_results"


class IngestionJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["crawl", "visit"]
    request_id: str
    enqueued_at: datetime
    crawl: CrawlRecord | None = None
    visit: VisitEvidence | None = None

    @model_validator(mode="after")
    def validate_job(self) -> IngestionJob:
        if self.kind == "crawl":
            if self.crawl is None or self.visit is not None:
                raise ValueError("crawl ingestion requires only crawl evidence")
            expected = crawl_ingestion_request_id(self.crawl.crawl_id)
        else:
            if self.visit is None or self.crawl is not None:
                raise ValueError("visit ingestion requires only visit evidence")
            expected = visit_ingestion_request_id(self.visit.visit.visit_id)
        if self.request_id != expected:
            raise ValueError("ingestion request_id does not match its evidence")
        return self

    @property
    def identity(self) -> UUID:
        if self.kind == "crawl":
            assert self.crawl is not None
            return self.crawl.crawl_id
        assert self.visit is not None
        return self.visit.visit.visit_id


class DeadLetterEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    job: IngestionJob
    error: str
    failed_at: datetime
    processing_failure_count: int


class IngestionState(BaseModel):
    model_config = ConfigDict(frozen=True)

    job: IngestionJob
    status: Literal["pending", "succeeded", "failed"]
    updated_at: datetime
    published_at: datetime | None = None
    result: IngestionWriteResult | None = None
    error: str | None = None
    processing_failure_count: int = 0

    @model_validator(mode="after")
    def validate_state(self) -> IngestionState:
        if self.status == "pending" and (
            self.result is not None or self.error is not None
        ):
            raise ValueError("pending ingestion cannot contain a terminal result")
        if self.status == "succeeded" and (
            self.result is None or self.error is not None
        ):
            raise ValueError("succeeded ingestion requires only a result")
        if self.status == "failed" and (
            self.error is None or self.result is not None
        ):
            raise ValueError("failed ingestion requires only an error")
        if self.result is not None and (
            self.result.kind != self.job.kind
            or self.result.identity != self.job.identity
        ):
            raise ValueError("ingestion result does not match its job")
        return self


def crawl_ingestion_request_id(crawl_id: UUID) -> str:
    return f"crawl-{crawl_id.hex}"


def visit_ingestion_request_id(visit_id: UUID) -> str:
    return f"visit-{visit_id.hex}"


def crawl_ingestion_job(record: CrawlRecord) -> IngestionJob:
    return IngestionJob(
        kind="crawl",
        request_id=crawl_ingestion_request_id(record.crawl_id),
        enqueued_at=datetime.now(UTC),
        crawl=record,
    )


def visit_ingestion_job(evidence: VisitEvidence) -> IngestionJob:
    return IngestionJob(
        kind="visit",
        request_id=visit_ingestion_request_id(evidence.visit.visit_id),
        enqueued_at=datetime.now(UTC),
        visit=evidence,
    )


def encode_dead_letter(entry: DeadLetterEntry) -> bytes:
    return zstandard.ZstdCompressor(level=3).compress(
        entry.model_dump_json().encode()
    )


def decode_dead_letter(payload: bytes) -> DeadLetterEntry:
    return DeadLetterEntry.model_validate_json(
        zstandard.ZstdDecompressor().decompress(payload)
    )


def _validate_envelope(payload: bytes, *, label: str) -> None:
    limit = get_int("PERIPLUS_NATS_MAX_ENVELOPE_BYTES")
    if len(payload) > limit:
        raise ValueError(f"{label} is {len(payload)} bytes; limit is {limit} bytes")


async def ensure_repository_stream(jetstream) -> None:
    await ensure_catalogue_work_stream(jetstream)


async def ensure_dead_letter_stream(jetstream) -> None:
    await ensure_catalogue_dead_letter_stream(jetstream)


async def ensure_ingestion_results(jetstream):
    try:
        bucket = await jetstream.key_value(RESULTS_BUCKET)
        await _validate_ingestion_results(bucket)
        return bucket
    except BucketNotFoundError:
        config = KeyValueConfig(
            bucket=RESULTS_BUCKET,
            description="Durable latest state for Periplus ingestion",
            history=1,
            ttl=get_float("PERIPLUS_INGEST_RESULT_TTL_SECONDS"),
            max_bytes=get_int("PERIPLUS_INGEST_RESULT_MAX_BYTES"),
            storage=StorageType.FILE,
            replicas=get_int("PERIPLUS_INGEST_RESULT_REPLICAS"),
        )
        try:
            bucket = await jetstream.create_key_value(config=config)
        except BadRequestError:
            bucket = await jetstream.key_value(RESULTS_BUCKET)
        await _validate_ingestion_results(bucket)
        return bucket


async def _validate_ingestion_results(bucket) -> None:
    await validate_kv_contract(
        bucket,
        name=RESULTS_BUCKET,
        ttl=get_float("PERIPLUS_INGEST_RESULT_TTL_SECONDS"),
        max_bytes=get_int("PERIPLUS_INGEST_RESULT_MAX_BYTES"),
        replicas=get_int("PERIPLUS_INGEST_RESULT_REPLICAS"),
    )


def repository_consumer_config() -> ConsumerConfig:
    return ConsumerConfig(
        durable_name=DURABLE,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=ack_wait_seconds(),
        filter_subject=SUBJECT,
        max_ack_pending=INGESTION_CONSUMER_MAX_ACK_PENDING,
        max_deliver=-1,
    )


async def ensure_repository_consumer(jetstream) -> None:
    expected = repository_consumer_config()
    try:
        info = await jetstream.consumer_info(STREAM, DURABLE)
    except NotFoundError:
        try:
            info = await jetstream.add_consumer(STREAM, config=expected)
        except BadRequestError:
            info = await jetstream.consumer_info(STREAM, DURABLE)
    config = info.config
    immutable_mismatches: list[str] = []
    if config.ack_policy != AckPolicy.EXPLICIT:
        immutable_mismatches.append("explicit acknowledgements")
    if config.filter_subject != SUBJECT:
        immutable_mismatches.append(f"filter_subject={SUBJECT}")
    if immutable_mismatches:
        raise RuntimeError(
            f"JetStream consumer {DURABLE} must use "
            + ", ".join(immutable_mismatches)
        )
    if (
        config.ack_wait != expected.ack_wait
        or config.max_ack_pending != expected.max_ack_pending
        or config.max_deliver != expected.max_deliver
    ):
        try:
            info = await jetstream.add_consumer(STREAM, config=expected)
        except BadRequestError:
            info = await jetstream.consumer_info(STREAM, DURABLE)
        config = info.config
    mismatches: list[str] = []
    if config.ack_wait != expected.ack_wait:
        mismatches.append(f"ack_wait={expected.ack_wait:g}s")
    if config.max_ack_pending != expected.max_ack_pending:
        mismatches.append(f"max_ack_pending={expected.max_ack_pending}")
    if config.max_deliver != -1:
        mismatches.append("unlimited server delivery")
    if mismatches:
        raise RuntimeError(
            f"JetStream consumer {DURABLE} must use " + ", ".join(mismatches)
        )


def max_delivery_attempts() -> int:
    return get_int("PERIPLUS_INGEST_MAX_DELIVER")


def ack_wait_seconds() -> float:
    return INGESTION_ACK_WAIT_SECONDS


async def get_ingestion_state(results, request_id: str) -> IngestionState | None:
    try:
        entry = await results.get(request_id)
    except (KeyNotFoundError, KeyDeletedError):
        return None
    return IngestionState.model_validate_json(entry.value)


async def ensure_pending_ingestion(
    results,
    *,
    job: IngestionJob,
) -> IngestionState:
    now = datetime.now(UTC)
    pending = IngestionState(
        job=job,
        status="pending",
        updated_at=now,
    )
    payload = pending.model_dump_json().encode()
    _validate_envelope(payload, label="ingestion pending-state envelope")
    try:
        await results.create(job.request_id, payload)
        return pending
    except KeyWrongLastSequenceError:
        existing = await get_ingestion_state(results, job.request_id)
        if existing is None:
            await results.create(job.request_id, payload)
            return pending
        if existing.job.model_copy(update={"enqueued_at": job.enqueued_at}) != job:
            raise RuntimeError(
                f"ingestion {job.request_id} already has different evidence"
            )
        return existing


async def store_ingestion_response(
    results,
    *,
    job: IngestionJob,
    result: IngestionWriteResult | None = None,
    error: str | None = None,
) -> IngestionState:
    if (result is None) == (error is None):
        raise ValueError("exactly one of result or error is required")
    while True:
        entry = await results.get(job.request_id)
        current = IngestionState.model_validate_json(entry.value)
        if current.status == "succeeded":
            return current
        if current.status == "failed" and result is None:
            return current
        state = current.model_copy(
            update={
                "status": "succeeded" if result is not None else "failed",
                "updated_at": datetime.now(UTC),
                "result": result,
                "error": error,
            }
        )
        payload = state.model_dump_json().encode()
        _validate_envelope(payload, label="ingestion terminal-state envelope")
        try:
            await results.update(job.request_id, payload, last=entry.revision)
            return state
        except KeyWrongLastSequenceError:
            continue


async def record_ingestion_processing_failure(results, request_id: str) -> int:
    while True:
        entry = await results.get(request_id)
        current = IngestionState.model_validate_json(entry.value)
        if current.status != "pending":
            return current.processing_failure_count
        updated = current.model_copy(
            update={
                "processing_failure_count": current.processing_failure_count + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        try:
            await results.update(
                request_id,
                updated.model_dump_json().encode(),
                last=entry.revision,
            )
            return updated.processing_failure_count
        except KeyWrongLastSequenceError:
            continue


async def mark_ingestion_published(results, request_id: str) -> IngestionState:
    while True:
        entry = await results.get(request_id)
        state = IngestionState.model_validate_json(entry.value)
        if state.status != "pending" or state.published_at is not None:
            return state
        now = datetime.now(UTC)
        published = state.model_copy(update={"published_at": now, "updated_at": now})
        payload = published.model_dump_json().encode()
        _validate_envelope(payload, label="ingestion published-state envelope")
        try:
            await results.update(request_id, payload, last=entry.revision)
            return published
        except KeyWrongLastSequenceError:
            continue


async def publish_dead_letter(
    jetstream,
    *,
    job: IngestionJob,
    error: str,
    processing_failure_count: int,
) -> None:
    entry = DeadLetterEntry(
        job=job,
        error=error,
        failed_at=datetime.now(UTC),
        processing_failure_count=processing_failure_count,
    )
    payload = encode_dead_letter(entry)
    _validate_envelope(payload, label="ingestion dead-letter envelope")
    await jetstream.publish(
        DEAD_LETTER_SUBJECT,
        payload,
        stream=DEAD_LETTER_STREAM,
        headers={
            "Nats-Msg-Id": (
                f"{job.request_id}:terminal:{processing_failure_count}:"
                f"{job.enqueued_at.isoformat()}"
            )
        },
    )


async def requeue_dead_letter(jetstream, results, sequence: int) -> DeadLetterEntry:
    raw = await jetstream.get_msg(DEAD_LETTER_STREAM, seq=sequence)
    if raw.subject != DEAD_LETTER_SUBJECT:
        raise RuntimeError("the sequence is not an ingestion dead letter")
    dead_letter = decode_dead_letter(raw.data)
    while True:
        try:
            entry = await results.get(dead_letter.job.request_id)
        except (KeyNotFoundError, KeyDeletedError):
            recovered = await ensure_pending_ingestion(
                results,
                job=dead_letter.job,
            )
            if recovered.status == "succeeded":
                raise RuntimeError("the ingestion has already succeeded")
            if recovered.status == "failed":
                continue
            pending = recovered
            break
        state = IngestionState.model_validate_json(entry.value)
        if state.status == "succeeded":
            raise RuntimeError("the ingestion has already succeeded")
        now = datetime.now(UTC)
        job = state.job.model_copy(update={"enqueued_at": now})
        pending = state.model_copy(
            update={
                "job": job,
                "status": "pending",
                "updated_at": now,
                "published_at": None,
                "result": None,
                "error": None,
                "processing_failure_count": 0,
            }
        )
        try:
            await results.update(
                state.job.request_id,
                pending.model_dump_json().encode(),
                last=entry.revision,
            )
            break
        except KeyWrongLastSequenceError:
            continue

    payload = pending.job.model_dump_json().encode()
    _validate_envelope(payload, label="ingestion requeue envelope")
    await jetstream.publish(
        SUBJECT,
        payload,
        stream=STREAM,
        headers={
            "Nats-Msg-Id": (
                f"{pending.job.request_id}:requeue:{sequence}"
            )
        },
    )
    await mark_ingestion_published(results, pending.job.request_id)
    deleted = await jetstream.delete_msg(DEAD_LETTER_STREAM, sequence)
    if not deleted:
        raise RuntimeError(f"dead-letter sequence {sequence} could not be removed")
    return dead_letter


def result_from_ingestion_state(
    state: IngestionState,
) -> IngestionWriteResult:
    if state.status == "failed":
        raise RuntimeError(state.error or "ingestion failed")
    if state.status != "succeeded" or state.result is None:
        raise RuntimeError("ingestion has not completed")
    return state.result


class IngestionQueueClient:
    def __init__(self) -> None:
        self.client = None
        self.jetstream = None
        self.results = None

    async def connect(self) -> None:
        self.client = await connect_nats()
        self.jetstream = self.client.jetstream()
        await ensure_repository_stream(self.jetstream)
        self.results = await ensure_ingestion_results(self.jetstream)

    async def enqueue_visit(self, evidence: VisitEvidence) -> None:
        await self.enqueue(visit_ingestion_job(evidence))

    async def enqueue_crawl(self, record: CrawlRecord) -> None:
        await self.enqueue(crawl_ingestion_job(record))

    async def enqueue(self, job: IngestionJob) -> None:
        state = await self._pending_state(job)
        if state.status != "pending" or state.published_at is not None:
            return
        await self._publish(state.job)
        await mark_ingestion_published(self.results, state.job.request_id)

    async def submit(self, job: IngestionJob) -> IngestionWriteResult:
        state = await self._pending_state(job)
        if state.status != "pending":
            return result_from_ingestion_state(state)
        return await self._publish_and_wait(state)

    async def resume(
        self,
        kind: Literal["crawl", "visit"],
        identity: UUID,
    ) -> IngestionWriteResult | None:
        self._require_connected()
        request_id = (
            crawl_ingestion_request_id(identity)
            if kind == "crawl"
            else visit_ingestion_request_id(identity)
        )
        state = await get_ingestion_state(self.results, request_id)
        if state is None:
            return None
        if state.status != "pending":
            return result_from_ingestion_state(state)
        return await self._publish_and_wait(state)

    async def _pending_state(self, job: IngestionJob) -> IngestionState:
        self._require_connected()
        return await ensure_pending_ingestion(self.results, job=job)

    async def _publish_and_wait(
        self,
        state: IngestionState,
    ) -> IngestionWriteResult:
        self._require_connected()
        poll_seconds = get_float("PERIPLUS_INGEST_RESULT_POLL_SECONDS")
        while True:
            durable = await get_ingestion_state(
                self.results,
                state.job.request_id,
            )
            if durable is not None and durable.status != "pending":
                return result_from_ingestion_state(durable)
            if durable is None or durable.published_at is None:
                await self._publish(state.job)
                durable = await mark_ingestion_published(
                    self.results,
                    state.job.request_id,
                )
                if durable.status != "pending":
                    return result_from_ingestion_state(durable)
            await asyncio.sleep(poll_seconds)

    async def _publish(self, job: IngestionJob) -> None:
        payload = job.model_dump_json().encode()
        _validate_envelope(payload, label="ingestion job")
        await self.jetstream.publish(
            SUBJECT,
            payload,
            stream=STREAM,
            headers={"Nats-Msg-Id": job.request_id},
        )

    def _require_connected(self) -> None:
        if self.client is None or self.jetstream is None or self.results is None:
            raise RuntimeError("ingestion queue is not connected")

    async def close(self) -> None:
        if self.client is not None:
            await self.client.drain()
            self.client = None
            self.jetstream = None
            self.results = None
