"""Corpus notifications and bounded material batches; object storage owns inputs."""

from periplus.platform.config import get_int
from nats.js.api import DiscardPolicy, RetentionPolicy, StorageType, StreamConfig
from periplus.platform.messaging.topology import ensure_stream_contract

WORK_STREAM = "PERIPLUS_CORPUS_EVENTS"
EVENT_SUBJECT = "periplus.corpus.event"
MATERIAL_STREAM = "PERIPLUS_MATERIAL_WORK"
MATERIAL_SUBJECT = "periplus.material.batch"


async def ensure_catalogue_work_stream(jetstream) -> None:
    replicas = get_int("PERIPLUS_CATALOGUE_WORK_STREAM_REPLICAS")
    # Notifications are an acceleration, not the archive. Bounds are explicit;
    # every target also reconciles the durable archive sequence checkpoints.
    await ensure_stream_contract(
        jetstream,
        StreamConfig(
            name=WORK_STREAM,
            subjects=[EVENT_SUBJECT],
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            num_replicas=replicas,
            max_age=86400,
            max_bytes=get_int("PERIPLUS_CATALOGUE_WORK_MAX_BYTES"),
            discard=DiscardPolicy.OLD,
        ),
    )
    await ensure_stream_contract(
        jetstream,
        StreamConfig(
            name=MATERIAL_STREAM,
            subjects=[MATERIAL_SUBJECT + ".*"],
            retention=RetentionPolicy.WORK_QUEUE,
            storage=StorageType.FILE,
            num_replicas=replicas,
            max_age=0,
            max_bytes=get_int("PERIPLUS_CATALOGUE_WORK_MAX_BYTES"),
            discard=DiscardPolicy.NEW,
        ),
    )
