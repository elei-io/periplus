"""Typed catch-up barriers and immutable consumer incarnation checks."""
from uuid import UUID
from pydantic import BaseModel, ConfigDict
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from nats.js.errors import NotFoundError
from periplus.ingestion.queue import STREAM, SUBJECT, DURABLE, ack_wait_seconds
from periplus.platform.messaging.catalogue_queue import BARRIER_SUBJECT


class Barrier(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    build_id: UUID


async def acknowledge_barrier(message) -> bool:
    if message.subject != BARRIER_SUBJECT:
        return False
    Barrier.model_validate_json(message.data)
    await message.ack_sync()
    return True


async def ensure_target_consumer(jetstream, build):
    try:
        info = await jetstream.consumer_info(STREAM, build.consumer)
    except NotFoundError:
        if build.consumer_created:
            raise ValueError('consumer_missing: create a new rebuild to re-establish coverage') from None
        info = await jetstream.add_consumer(STREAM, config=ConsumerConfig(
            durable_name=build.consumer, filter_subject=SUBJECT, ack_policy=AckPolicy.EXPLICIT,
            deliver_policy=DeliverPolicy.ALL, ack_wait=ack_wait_seconds(), max_ack_pending=8, max_deliver=-1))
    stream = await jetstream.stream_info(STREAM)
    ingestion = await jetstream.consumer_info(STREAM, DURABLE)
    actual = (stream.created.isoformat(), info.created.isoformat(), ingestion.created.isoformat())
    expected = (build.stream_created, build.consumer_created, build.ingestion_created)
    if any(old is not None and old != new for old, new in zip(expected, actual, strict=True)):
        raise ValueError('delivery_incarnation_changed: create a new rebuild')
    if info.config.filter_subject != SUBJECT:
        raise ValueError('material consumer filter differs from the ingestion coverage contract')
    return actual, info, ingestion
