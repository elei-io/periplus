"""Startup-only reconciliation of the crawler's single bounded capture lane."""
from datetime import datetime
from uuid import UUID

from nats.js.api import AckPolicy, ConsumerConfig, DiscardPolicy, KeyValueConfig, RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import BadRequestError, BucketNotFoundError, NotFoundError
from pydantic import BaseModel, ConfigDict, Field
from periplus.platform.messaging.topology import operational_replicas
from periplus.crawl.runtime.frontier_health import DispatchReadiness

from periplus.platform.config import get_float
from periplus.platform.messaging.topology import validate_kv_contract

CAPTURE_STREAM = "PERIPLUS_CRAWL_WORK"
CAPTURE_SUBJECT = "periplus.crawl.capture"
CAPTURE_CONSUMER = "periplus-capture"
CAPTURE_MAX_PENDING = 128
CAPTURE_ACK_WAIT = 120


class CaptureWork(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    acquisition_id: UUID
    generation: int = Field(ge=1)

    @property
    def message_id(self) -> str:
        return f"capture:{self.acquisition_id}:{self.generation}"


async def ensure_capture_queue(jetstream, *, replicas: int = 1,
                               max_bytes: int = 32 * 1024 * 1024) -> None:
    if not 1 <= replicas <= 5 or max_bytes < 1024:
        raise ValueError("invalid capture stream capacity")
    expected_stream = StreamConfig(
        name=CAPTURE_STREAM, subjects=[CAPTURE_SUBJECT], storage=StorageType.FILE,
        retention=RetentionPolicy.WORK_QUEUE, discard=DiscardPolicy.NEW,
        num_replicas=replicas, max_bytes=max_bytes,
    )
    try:
        stream = await jetstream.stream_info(CAPTURE_STREAM)
    except NotFoundError:
        try:
            await jetstream.add_stream(config=expected_stream)
        except BadRequestError:
            # A symmetric replica may have installed the same contract.
            pass
        stream = await jetstream.stream_info(CAPTURE_STREAM)
    for field in ("subjects", "storage", "retention", "discard", "num_replicas", "max_bytes"):
        if getattr(stream.config, field) != getattr(expected_stream, field):
            raise RuntimeError(f"capture stream contract differs: {field}; reset disposable delivery state")
    expected_consumer = ConsumerConfig(
        durable_name=CAPTURE_CONSUMER, filter_subject=CAPTURE_SUBJECT,
        ack_policy=AckPolicy.EXPLICIT, ack_wait=CAPTURE_ACK_WAIT,
        max_ack_pending=CAPTURE_MAX_PENDING, max_deliver=-1,
    )
    try:
        consumer = await jetstream.consumer_info(CAPTURE_STREAM, CAPTURE_CONSUMER)
    except NotFoundError:
        try:
            await jetstream.add_consumer(CAPTURE_STREAM, config=expected_consumer)
        except BadRequestError:
            pass
        consumer = await jetstream.consumer_info(CAPTURE_STREAM, CAPTURE_CONSUMER)
    for field in ("filter_subject", "ack_policy", "ack_wait", "max_ack_pending", "max_deliver"):
        if getattr(consumer.config, field) != getattr(expected_consumer, field):
            raise RuntimeError(f"capture consumer contract differs: {field}; reset disposable delivery state")


# This replaces graph-worker presence at cutover. It advertises configured local
# lanes only; authoritative active acquisitions are read from the frontier.
CRAWLER_PRESENCE_BUCKET = "periplus_crawler_workers"


class CrawlerPresence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    worker_id: str
    started_at: datetime
    last_seen_at: datetime
    capture_lanes: int = Field(ge=1, le=48)
    dispatch: DispatchReadiness


async def ensure_crawler_presence(jetstream):
    config = KeyValueConfig(
        bucket=CRAWLER_PRESENCE_BUCKET, description="Ephemeral Periplus crawler presence",
        history=1, ttl=get_float("PERIPLUS_CRAWLER_PRESENCE_TTL_SECONDS"),
        max_bytes=1024 * 1024, storage=StorageType.FILE, replicas=operational_replicas(),
    )
    try:
        bucket = await jetstream.key_value(CRAWLER_PRESENCE_BUCKET)
    except BucketNotFoundError:
        try:
            bucket = await jetstream.create_key_value(config=config)
        except BadRequestError:
            bucket = await jetstream.key_value(CRAWLER_PRESENCE_BUCKET)
    await validate_kv_contract(bucket, name=config.bucket, ttl=config.ttl,
                               max_bytes=config.max_bytes, replicas=config.replicas)
    return bucket
