"""JetStream work queue and durable result contracts for repository ingestion."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from config import get_float, get_int
from config.performance import INGESTION_ACK_WAIT_SECONDS, INGESTION_CONSUMER_MAX_ACK_PENDING
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    KeyValueConfig,
    StorageType,
)
from nats.js.errors import (
    BadRequestError,
    BucketNotFoundError,
    KeyDeletedError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)
from pydantic import BaseModel, ConfigDict, model_validator
import zstandard

from repository.catalogue import (
    CatalogueWriteResult,
    CrawlRecord,
    CrawlStepRecord,
)
from runtime.catalogue_queue import (
    DEAD_LETTER_STREAM,
    INGEST_DEAD_LETTER_SUBJECT as DEAD_LETTER_SUBJECT,
    INGEST_SUBJECT as SUBJECT,
    WORK_STREAM as STREAM,
    ensure_catalogue_work_stream,
    ensure_dead_letter_stream as ensure_catalogue_dead_letter_stream,
)
from runtime.nats_client import connect_nats

DURABLE = "atlas-repository-writer"
RESULTS_BUCKET = "atlas_repository_results"


class IngestionJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    enqueued_at: datetime
    crawl: CrawlRecord
    crawl_steps: tuple[CrawlStepRecord, ...] | None = ()


class DeadLetterEntry(BaseModel):
    """Durable operator-facing record of a terminal ingestion failure."""

    model_config = ConfigDict(frozen=True)

    job: IngestionJob
    error: str
    failed_at: datetime
    processing_failure_count: int


def encode_dead_letter(entry: DeadLetterEntry) -> bytes:
    return zstandard.ZstdCompressor(level=3).compress(entry.model_dump_json().encode())


def decode_dead_letter(payload: bytes) -> DeadLetterEntry:
    value = zstandard.ZstdDecompressor().decompress(payload)
    return DeadLetterEntry.model_validate_json(value)


class IngestionState(BaseModel):
    """Durable latest state for one retry-stable ingestion operation."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    status: Literal["pending", "succeeded", "failed"]
    crawl: CrawlRecord
    crawl_steps: tuple[CrawlStepRecord, ...] | None = ()
    enqueued_at: datetime
    updated_at: datetime
    published_at: datetime | None = None
    result: CatalogueWriteResult | None = None
    error: str | None = None
    processing_failure_count: int = 0

    @model_validator(mode="after")
    def validate_state(self) -> IngestionState:
        if self.status == "pending" and (
            self.result is not None or self.error is not None
        ):
            raise ValueError("pending ingestion cannot contain a terminal result")
        if self.status == "succeeded" and (self.result is None or self.error is not None):
            raise ValueError("succeeded ingestion requires only a result")
        if self.status == "failed" and (
            self.error is None or self.result is not None
        ):
            raise ValueError("failed ingestion requires only an error")
        return self


def crawl_ingestion_request_id(crawl_id: UUID) -> str:
    """Return the stable operation key for a crawl's initial repository commit."""

    return f"crawl-{crawl_id.hex}"


def projection_ingestion_request_id(document_id: str) -> str:
    """Return a stable operation key for the active DOM projection recipe."""

    from dom import DOM_SCHEMA_VERSION, PARSER_NAME, PARSER_OPTIONS_HASH, PARSER_VERSION

    value = (
        f"{document_id}\0{DOM_SCHEMA_VERSION}\0{PARSER_NAME}\0"
        f"{PARSER_VERSION}\0{PARSER_OPTIONS_HASH}"
    )
    return "projection-" + hashlib.sha256(value.encode()).hexdigest()


def _validate_envelope(payload: bytes, *, label: str) -> None:
    limit = get_int("ATLAS_NATS_MAX_ENVELOPE_BYTES")
    if len(payload) > limit:
        raise ValueError(f"{label} is {len(payload)} bytes; limit is {limit} bytes")


async def ensure_repository_stream(jetstream) -> None:
    await ensure_catalogue_work_stream(jetstream)


async def ensure_dead_letter_stream(jetstream) -> None:
    """Attach or create the durable operator-managed ingestion failure stream."""
    await ensure_catalogue_dead_letter_stream(jetstream)


async def ensure_ingestion_results(jetstream):
    """Attach or create the bounded file-backed ingestion result KV bucket."""

    try:
        bucket = await jetstream.key_value(RESULTS_BUCKET)
        await _validate_ingestion_results(bucket)
        return bucket
    except BucketNotFoundError:
        config = KeyValueConfig(
            bucket=RESULTS_BUCKET,
            description="Durable latest state for Atlas repository ingestion",
            history=1,
            ttl=get_float("ATLAS_INGEST_RESULT_TTL_SECONDS"),
            max_bytes=get_int("ATLAS_INGEST_RESULT_MAX_BYTES"),
            storage=StorageType.FILE,
            replicas=get_int("ATLAS_INGEST_RESULT_REPLICAS"),
        )
        try:
            bucket = await jetstream.create_key_value(config=config)
        except BadRequestError:
            # Another API/worker may have created the deployment-global bucket.
            bucket = await jetstream.key_value(RESULTS_BUCKET)
        await _validate_ingestion_results(bucket)
        return bucket


async def _validate_ingestion_results(bucket) -> None:
    status = await bucket.status()
    config = status.stream_info.config
    expected_ttl = get_float("ATLAS_INGEST_RESULT_TTL_SECONDS")
    expected_max_bytes = get_int("ATLAS_INGEST_RESULT_MAX_BYTES")
    expected_replicas = get_int("ATLAS_INGEST_RESULT_REPLICAS")
    mismatches: list[str] = []
    if config.storage != StorageType.FILE:
        mismatches.append("file storage")
    if config.max_msgs_per_subject != 1:
        mismatches.append("history=1")
    if config.max_age != expected_ttl:
        mismatches.append(f"ttl={expected_ttl:g}s")
    if config.max_bytes != expected_max_bytes:
        mismatches.append(f"max_bytes={expected_max_bytes}")
    if config.num_replicas != expected_replicas:
        mismatches.append(f"replicas={expected_replicas}")
    if mismatches:
        raise RuntimeError(
            f"JetStream KV {RESULTS_BUCKET} must use " + ", ".join(mismatches)
        )


def repository_consumer_config() -> ConsumerConfig:
    return ConsumerConfig(
        durable_name=DURABLE,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=ack_wait_seconds(),
        filter_subject=SUBJECT,
        max_ack_pending=INGESTION_CONSUMER_MAX_ACK_PENDING,
        # The application terminates a message only after a terminal result is durable.
        # Unlimited server delivery prevents a KV outage at the attempt boundary from
        # silently stranding a message without either work or a durable failure state.
        max_deliver=-1,
    )


async def ensure_repository_consumer(jetstream) -> None:
    """Create or reconcile the writer consumer, then verify its safety contract."""

    expected = repository_consumer_config()
    await jetstream.add_consumer(STREAM, config=expected)
    info = await jetstream.consumer_info(STREAM, DURABLE)
    config = info.config
    mismatches: list[str] = []
    if config.ack_policy != AckPolicy.EXPLICIT:
        mismatches.append("explicit acknowledgements")
    if config.filter_subject != SUBJECT:
        mismatches.append(f"filter_subject={SUBJECT}")
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
    return get_int("ATLAS_INGEST_MAX_DELIVER")


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
    request_id: str,
    crawl: CrawlRecord,
    crawl_steps: tuple[CrawlStepRecord, ...] | None = (),
) -> IngestionState:
    """Create pending state once, retaining the first frozen crawl envelope."""

    now = datetime.now(UTC)
    pending = IngestionState(
        request_id=request_id,
        status="pending",
        crawl=crawl,
        crawl_steps=crawl_steps,
        enqueued_at=now,
        updated_at=now,
    )
    try:
        payload = pending.model_dump_json().encode()
        _validate_envelope(payload, label="repository pending-state envelope")
        await results.create(request_id, payload)
        return pending
    except KeyWrongLastSequenceError:
        existing = await get_ingestion_state(results, request_id)
        if existing is None:
            # A delete/expiry raced the create. Retrying once creates a new generation.
            await results.create(request_id, payload)
            return pending
        return existing


async def store_ingestion_response(
    results,
    *,
    job: IngestionJob,
    result: CatalogueWriteResult | None = None,
    error: str | None = None,
) -> IngestionState:
    """Revision-fence a terminal transition before acknowledging the work message.

    A durable success is absorbing. A success discovered after a recorded failure may
    repair that failure, but a failure may only replace pending state. This lets a
    delivery that committed DuckLake recover the operation without allowing a stale
    duplicate failure to erase durable truth.
    """

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
        _validate_envelope(payload, label="repository terminal-state envelope")
        try:
            await results.update(
                job.request_id,
                payload,
                last=entry.revision,
            )
            return state
        except KeyWrongLastSequenceError:
            # Another delivery completed the operation from the same observed
            # revision. Re-evaluate the transition against the new durable state.
            continue


async def record_ingestion_processing_failure(results, request_id: str) -> int:
    """Count one admitted processing failure without counting redelivery."""

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
    """CAS a PubAck marker without overwriting a concurrently terminal result."""

    while True:
        entry = await results.get(request_id)
        state = IngestionState.model_validate_json(entry.value)
        if state.status != "pending" or state.published_at is not None:
            return state
        now = datetime.now(UTC)
        published = state.model_copy(update={"published_at": now, "updated_at": now})
        payload = published.model_dump_json().encode()
        _validate_envelope(payload, label="repository published-state envelope")
        try:
            await results.update(
                request_id,
                payload,
                last=entry.revision,
            )
            return published
        except KeyWrongLastSequenceError:
            # The worker may have made the operation terminal between our read
            # and update. Re-read rather than replacing terminal truth.
            continue


async def publish_dead_letter(
    jetstream,
    *,
    job: IngestionJob,
    error: str,
    processing_failure_count: int,
) -> None:
    """Persist a terminal failure before its work-queue message is removed."""

    entry = DeadLetterEntry(
        job=job,
        error=error,
        failed_at=datetime.now(UTC),
        processing_failure_count=processing_failure_count,
    )
    payload = encode_dead_letter(entry)
    _validate_envelope(payload, label="repository dead-letter envelope")
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
    """Reset terminal KV state, republish the frozen job, then remove its DLQ entry."""

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
                request_id=dead_letter.job.request_id,
                crawl=dead_letter.job.crawl,
                crawl_steps=dead_letter.job.crawl_steps,
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
        pending = state.model_copy(
            update={
                "status": "pending",
                "enqueued_at": now,
                "updated_at": now,
                "published_at": None,
                "result": None,
                "error": None,
                "processing_failure_count": 0,
            }
        )
        try:
            await results.update(
                state.request_id,
                pending.model_dump_json().encode(),
                last=entry.revision,
            )
            break
        except KeyWrongLastSequenceError:
            continue

    job = dead_letter.job.model_copy(update={"enqueued_at": pending.enqueued_at})
    payload = job.model_dump_json().encode()
    _validate_envelope(payload, label="repository requeue envelope")
    await jetstream.publish(
        SUBJECT,
        payload,
        stream=STREAM,
        headers={"Nats-Msg-Id": f"{job.request_id}:requeue:{sequence}"},
    )
    await mark_ingestion_published(results, job.request_id)
    deleted = await jetstream.delete_msg(DEAD_LETTER_STREAM, sequence)
    if not deleted:
        raise RuntimeError(f"dead-letter sequence {sequence} could not be removed")
    return dead_letter


def result_from_ingestion_state(state: IngestionState) -> CatalogueWriteResult:
    if state.status == "failed":
        raise RuntimeError(state.error or "repository ingestion failed")
    if state.status != "succeeded" or state.result is None:
        raise RuntimeError("repository ingestion has not completed")
    if not isinstance(state.result, CatalogueWriteResult):
        raise RuntimeError("repository ingestion returned a non-crawl result")
    return state.result


def _terminal_result(
    state: IngestionState,
) -> CatalogueWriteResult:
    if state.status == "failed":
        raise RuntimeError(state.error or "repository ingestion failed")
    if state.status != "succeeded" or state.result is None:
        raise RuntimeError("repository ingestion has not completed")
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

    async def submit(
        self,
        crawl: CrawlRecord,
        *,
        request_id: str | None = None,
        crawl_steps: tuple[CrawlStepRecord, ...] | None = (),
    ) -> CatalogueWriteResult:
        """Publish or resume one operation and wait on its durable state."""

        request_id = request_id or crawl_ingestion_request_id(crawl.crawl_id)
        state = await self._pending_state(
            request_id=request_id,
            crawl=crawl,
            crawl_steps=crawl_steps,
        )
        if state.status != "pending":
            return result_from_ingestion_state(state)
        result = await self._publish_and_wait(state)
        return result

    async def enqueue(
        self,
        crawl: CrawlRecord,
        *,
        request_id: str | None = None,
        crawl_steps: tuple[CrawlStepRecord, ...] | None = (),
    ) -> None:
        """Durably publish one operation without occupying acquisition capacity."""

        request_id = request_id or crawl_ingestion_request_id(crawl.crawl_id)
        state = await self._pending_state(
            request_id=request_id,
            crawl=crawl,
            crawl_steps=crawl_steps,
        )
        if state.status != "pending" or state.published_at is not None:
            return
        job = IngestionJob(
            request_id=state.request_id,
            enqueued_at=state.enqueued_at,
            crawl=state.crawl,
            crawl_steps=state.crawl_steps,
        )
        payload = job.model_dump_json().encode()
        _validate_envelope(payload, label="repository ingestion job")
        await self.jetstream.publish(
            SUBJECT,
            payload,
            stream=STREAM,
            headers={"Nats-Msg-Id": state.request_id},
        )
        await mark_ingestion_published(self.results, state.request_id)

    async def resume(self, crawl_id: UUID) -> CatalogueWriteResult | None:
        """Wait for a previously published initial crawl ingestion, if present."""

        self._require_connected()
        request_id = crawl_ingestion_request_id(crawl_id)
        state = await get_ingestion_state(self.results, request_id)
        if state is None:
            return None
        if state.status != "pending":
            return result_from_ingestion_state(state)
        return await self._publish_and_wait(state)

    async def _pending_state(
        self,
        *,
        request_id: str,
        crawl: CrawlRecord,
        crawl_steps: tuple[CrawlStepRecord, ...] | None = (),
    ) -> IngestionState:
        self._require_connected()
        return await ensure_pending_ingestion(
            self.results,
            request_id=request_id,
            crawl=crawl,
            crawl_steps=crawl_steps,
        )

    async def _publish_and_wait(
        self, state: IngestionState
    ) -> CatalogueWriteResult:
        self._require_connected()
        job = IngestionJob(
            request_id=state.request_id,
            enqueued_at=state.enqueued_at,
            crawl=state.crawl,
            crawl_steps=state.crawl_steps,
        )
        poll_seconds = get_float("ATLAS_INGEST_RESULT_POLL_SECONDS")
        while True:
            durable = await get_ingestion_state(self.results, state.request_id)
            if durable is not None and durable.status != "pending":
                return _terminal_result(durable)

            if durable is None or durable.published_at is None:
                # PubAck proves the work stream accepted the operation. The stable
                # message ID suppresses the only ambiguous duplicate: a producer
                # crash after PubAck but before the CAS marker below.
                payload = job.model_dump_json().encode()
                _validate_envelope(payload, label="repository ingestion job")
                await self.jetstream.publish(
                    SUBJECT,
                    payload,
                    stream=STREAM,
                    headers={"Nats-Msg-Id": state.request_id},
                )
                durable = await mark_ingestion_published(
                    self.results,
                    state.request_id,
                )
                if durable.status != "pending":
                    return _terminal_result(durable)

            await asyncio.sleep(poll_seconds)

    def _require_connected(self) -> None:
        if self.client is None or self.jetstream is None or self.results is None:
            raise RuntimeError("repository ingestion queue is not connected")

    async def close(self) -> None:
        if self.client is not None:
            await self.client.drain()
            self.client = None
            self.jetstream = None
            self.results = None
