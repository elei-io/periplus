"""JetStream work queue and durable result contracts for repository ingestion."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import nats
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    KeyValueConfig,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
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

from repository.ducklake import (
    CatalogueWriteResult,
    CrawlRecord,
    RunCrawlUsageRecord,
    RunManifestRecord,
    RunManifestWriteResult,
)

STREAM = "ATLAS_REPOSITORY"
SUBJECT = "atlas.repository.ingest"
DURABLE = "atlas-repository-writer"
RESULTS_BUCKET = "ATLAS_REPOSITORY_RESULTS"
DEAD_LETTER_STREAM = "ATLAS_REPOSITORY_DEAD_LETTER"
DEAD_LETTER_SUBJECT = "atlas.repository.dead_letter"


class IngestionJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    reply_subject: str
    enqueued_at: datetime
    kind: Literal["crawl", "manifest"] = "crawl"
    crawl: CrawlRecord | None = None
    run_manifest_zstd: str | None = None
    run_usage: RunCrawlUsageRecord | None = None

    @model_validator(mode="after")
    def validate_run_usage_pair(self) -> IngestionJob:
        if self.kind == "crawl" and self.crawl is None:
            raise ValueError("crawl ingestion requires a crawl")
        if self.kind == "manifest" and (
            self.crawl is not None or self.run_usage is not None or self.run_manifest_zstd is None
        ):
            raise ValueError("manifest ingestion requires only run_manifest_zstd")
        if self.kind == "crawl" and self.run_manifest_zstd is not None:
            raise ValueError("crawl ingestion must not embed a run manifest")
        return self


class IngestionResponse(BaseModel):
    """Small notification payload; durable truth lives in ``IngestionState``."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    result: CatalogueWriteResult | RunManifestWriteResult | None = None
    error: str | None = None


class DeadLetterEntry(BaseModel):
    """Durable operator-facing record of a terminal ingestion failure."""

    model_config = ConfigDict(frozen=True)

    job: IngestionJob
    error: str
    failed_at: datetime
    delivery_count: int


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
    kind: Literal["crawl", "manifest"] = "crawl"
    crawl: CrawlRecord | None = None
    run_manifest_zstd: str | None = None
    run_usage: RunCrawlUsageRecord | None = None
    enqueued_at: datetime
    updated_at: datetime
    published_at: datetime | None = None
    result: CatalogueWriteResult | RunManifestWriteResult | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> IngestionState:
        if self.kind == "crawl" and self.crawl is None:
            raise ValueError("crawl ingestion requires a crawl")
        if self.kind == "manifest" and (
            self.crawl is not None or self.run_usage is not None or self.run_manifest_zstd is None
        ):
            raise ValueError("manifest ingestion requires only run_manifest_zstd")
        if self.status == "pending" and (self.result is not None or self.error is not None):
            raise ValueError("pending ingestion cannot contain a terminal result")
        if self.status == "succeeded" and (self.result is None or self.error is not None):
            raise ValueError("succeeded ingestion requires only a result")
        if self.status == "failed" and (self.error is None or self.result is not None):
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


def run_usage_ingestion_request_id(usage_id: UUID) -> str:
    return f"usage-{usage_id.hex}"


def run_manifest_ingestion_request_id(run_id: UUID) -> str:
    return f"manifest-{run_id.hex}"


def encode_run_manifest(manifest: RunManifestRecord) -> str:
    payload = manifest.model_dump_json().encode()
    compressed = zstandard.ZstdCompressor(level=3).compress(payload)
    return base64.b64encode(compressed).decode("ascii")


def decode_run_manifest(value: str) -> RunManifestRecord:
    compressed = base64.b64decode(value, validate=True)
    payload = zstandard.ZstdDecompressor().decompress(compressed)
    return RunManifestRecord.model_validate_json(payload)


def _validate_envelope(payload: bytes, *, label: str) -> None:
    limit = _positive_int("ATLAS_NATS_MAX_ENVELOPE_BYTES", 900 * 1024)
    if len(payload) > limit:
        raise ValueError(f"{label} is {len(payload)} bytes; limit is {limit} bytes")


async def connect_repository_nats():
    return await nats.connect(
        os.getenv("NATS_URL", "nats://127.0.0.1:4222"),
        connect_timeout=2,
        max_reconnect_attempts=-1,
    )


async def ensure_repository_stream(jetstream) -> None:
    replicas = _positive_int("ATLAS_INGEST_STREAM_REPLICAS", 1)
    config = StreamConfig(
        name=STREAM,
        subjects=[SUBJECT],
        retention=RetentionPolicy.WORK_QUEUE,
        storage=StorageType.FILE,
        num_replicas=replicas,
    )
    try:
        info = await jetstream.stream_info(STREAM)
    except NotFoundError:
        await jetstream.add_stream(config=config)
        return
    if set(info.config.subjects) != {SUBJECT}:
        raise RuntimeError(f"JetStream {STREAM} must capture only {SUBJECT}")
    if info.config.retention != RetentionPolicy.WORK_QUEUE:
        raise RuntimeError(f"JetStream {STREAM} must use work-queue retention")
    if info.config.storage != StorageType.FILE:
        raise RuntimeError(f"JetStream {STREAM} must use file storage")
    if info.config.num_replicas != replicas:
        raise RuntimeError(f"JetStream {STREAM} must have {replicas} replicas")


async def ensure_dead_letter_stream(jetstream) -> None:
    """Attach or create the durable operator-managed ingestion failure stream."""

    replicas = _positive_int("ATLAS_INGEST_STREAM_REPLICAS", 1)
    config = StreamConfig(
        name=DEAD_LETTER_STREAM,
        subjects=[DEAD_LETTER_SUBJECT],
        retention=RetentionPolicy.LIMITS,
        storage=StorageType.FILE,
        num_replicas=replicas,
    )
    try:
        info = await jetstream.stream_info(DEAD_LETTER_STREAM)
    except NotFoundError:
        await jetstream.add_stream(config=config)
        return
    if set(info.config.subjects) != {DEAD_LETTER_SUBJECT}:
        raise RuntimeError(
            f"JetStream {DEAD_LETTER_STREAM} must capture only {DEAD_LETTER_SUBJECT}"
        )
    if info.config.retention != RetentionPolicy.LIMITS:
        raise RuntimeError(f"JetStream {DEAD_LETTER_STREAM} must use limits retention")
    if info.config.storage != StorageType.FILE:
        raise RuntimeError(f"JetStream {DEAD_LETTER_STREAM} must use file storage")
    if info.config.num_replicas != replicas:
        raise RuntimeError(
            f"JetStream {DEAD_LETTER_STREAM} must have {replicas} replicas"
        )


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
            ttl=_positive_float("ATLAS_INGEST_RESULT_TTL_SECONDS", 7 * 24 * 60 * 60),
            max_bytes=_positive_int("ATLAS_INGEST_RESULT_MAX_BYTES", 256 * 1024 * 1024),
            storage=StorageType.FILE,
            replicas=_positive_int("ATLAS_INGEST_RESULT_REPLICAS", 1),
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
    expected_ttl = _positive_float(
        "ATLAS_INGEST_RESULT_TTL_SECONDS", 7 * 24 * 60 * 60
    )
    expected_max_bytes = _positive_int(
        "ATLAS_INGEST_RESULT_MAX_BYTES", 256 * 1024 * 1024
    )
    expected_replicas = _positive_int("ATLAS_INGEST_RESULT_REPLICAS", 1)
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
        max_ack_pending=_positive_int("ATLAS_INGEST_MAX_ACK_PENDING", 1000),
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
    return _positive_int("ATLAS_INGEST_MAX_DELIVER", 5)


def ack_wait_seconds() -> float:
    return _positive_float("ATLAS_INGEST_ACK_WAIT_SECONDS", 600)


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
    crawl: CrawlRecord | None = None,
    kind: Literal["crawl", "manifest"] = "crawl",
    run_manifest_zstd: str | None = None,
    run_usage: RunCrawlUsageRecord | None = None,
) -> IngestionState:
    """Create pending state once, retaining the first frozen crawl envelope."""

    now = datetime.now(UTC)
    pending = IngestionState(
        request_id=request_id,
        status="pending",
        kind=kind,
        crawl=crawl,
        run_manifest_zstd=run_manifest_zstd,
        run_usage=run_usage,
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
    result: CatalogueWriteResult | RunManifestWriteResult | None = None,
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
    delivery_count: int,
) -> None:
    """Persist a terminal failure before its work-queue message is removed."""

    entry = DeadLetterEntry(
        job=job,
        error=error,
        failed_at=datetime.now(UTC),
        delivery_count=delivery_count,
    )
    payload = encode_dead_letter(entry)
    _validate_envelope(payload, label="repository dead-letter envelope")
    await jetstream.publish(
        DEAD_LETTER_SUBJECT,
        payload,
        stream=DEAD_LETTER_STREAM,
        headers={
            "Nats-Msg-Id": (
                f"{job.request_id}:terminal:{delivery_count}:"
                f"{job.enqueued_at.isoformat()}"
            )
        },
    )


async def requeue_dead_letter(jetstream, results, sequence: int) -> DeadLetterEntry:
    """Reset terminal KV state, republish the frozen job, then remove its DLQ entry."""

    raw = await jetstream.get_msg(DEAD_LETTER_STREAM, seq=sequence)
    dead_letter = decode_dead_letter(raw.data)
    while True:
        try:
            entry = await results.get(dead_letter.job.request_id)
        except (KeyNotFoundError, KeyDeletedError):
            recovered = await ensure_pending_ingestion(
                results,
                request_id=dead_letter.job.request_id,
                kind=dead_letter.job.kind,
                crawl=dead_letter.job.crawl,
                run_manifest_zstd=dead_letter.job.run_manifest_zstd,
                run_usage=dead_letter.job.run_usage,
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


def ingestion_response(state: IngestionState) -> IngestionResponse:
    if state.status == "pending":
        raise ValueError("pending ingestion has no terminal response")
    return IngestionResponse(
        request_id=state.request_id,
        result=state.result,
        error=state.error,
    )


def result_from_ingestion_state(state: IngestionState) -> CatalogueWriteResult:
    if state.status == "failed":
        raise RuntimeError(state.error or "repository ingestion failed")
    if state.status != "succeeded" or state.result is None:
        raise RuntimeError("repository ingestion has not completed")
    if not isinstance(state.result, CatalogueWriteResult):
        raise RuntimeError("repository ingestion returned a non-crawl result")
    return state.result


def manifest_result_from_ingestion_state(state: IngestionState) -> RunManifestWriteResult:
    if state.status == "failed":
        raise RuntimeError(state.error or "run manifest ingestion failed")
    if state.status != "succeeded" or not isinstance(state.result, RunManifestWriteResult):
        raise RuntimeError("run manifest ingestion has not completed")
    return state.result


def _terminal_result(
    state: IngestionState,
) -> CatalogueWriteResult | RunManifestWriteResult:
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
        self.client = await connect_repository_nats()
        self.jetstream = self.client.jetstream()
        await ensure_repository_stream(self.jetstream)
        self.results = await ensure_ingestion_results(self.jetstream)

    async def submit(
        self,
        crawl: CrawlRecord,
        *,
        request_id: str | None = None,
        run_usage: RunCrawlUsageRecord | None = None,
    ) -> CatalogueWriteResult:
        """Publish/resume one operation and wait on durable state, not its inbox."""

        request_id = request_id or crawl_ingestion_request_id(crawl.crawl_id)
        state = await self._pending_state(
            request_id=request_id,
            crawl=crawl,
            run_usage=run_usage,
        )
        if state.status != "pending":
            return result_from_ingestion_state(state)
        result = await self._publish_and_wait(state)
        if not isinstance(result, CatalogueWriteResult):
            raise RuntimeError("repository crawl ingestion returned a manifest result")
        return result

    async def submit_manifest(self, manifest: RunManifestRecord) -> RunManifestWriteResult:
        encoded = encode_run_manifest(manifest)
        state = await self._pending_state(
            request_id=run_manifest_ingestion_request_id(manifest.run_id),
            kind="manifest",
            run_manifest_zstd=encoded,
        )
        if state.status != "pending":
            return manifest_result_from_ingestion_state(state)
        result = await self._publish_and_wait(state)
        if not isinstance(result, RunManifestWriteResult):
            raise RuntimeError("run manifest ingestion returned a crawl result")
        return result

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
        crawl: CrawlRecord | None = None,
        kind: Literal["crawl", "manifest"] = "crawl",
        run_manifest_zstd: str | None = None,
        run_usage: RunCrawlUsageRecord | None = None,
    ) -> IngestionState:
        self._require_connected()
        return await ensure_pending_ingestion(
            self.results,
            request_id=request_id,
            kind=kind,
            crawl=crawl,
            run_manifest_zstd=run_manifest_zstd,
            run_usage=run_usage,
        )

    async def _publish_and_wait(
        self, state: IngestionState
    ) -> CatalogueWriteResult | RunManifestWriteResult:
        self._require_connected()
        reply_subject = self.client.new_inbox()
        subscription = await self.client.subscribe(reply_subject)
        job = IngestionJob(
            request_id=state.request_id,
            reply_subject=reply_subject,
            enqueued_at=state.enqueued_at,
            kind=state.kind,
            crawl=state.crawl,
            run_manifest_zstd=state.run_manifest_zstd,
            run_usage=state.run_usage,
        )
        poll_seconds = _positive_float("ATLAS_INGEST_RESULT_POLL_SECONDS", 0.5)
        try:
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

                try:
                    # The inbox only wakes the poll early. Its payload is never trusted
                    # as terminal truth, and losing it cannot lose the result.
                    await subscription.next_msg(timeout=poll_seconds)
                except (NatsTimeoutError, asyncio.TimeoutError):
                    pass
        finally:
            await subscription.unsubscribe()

    def _require_connected(self) -> None:
        if self.client is None or self.jetstream is None or self.results is None:
            raise RuntimeError("repository ingestion queue is not connected")

    async def close(self) -> None:
        if self.client is not None:
            await self.client.drain()
            self.client = None
            self.jetstream = None
            self.results = None


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value
