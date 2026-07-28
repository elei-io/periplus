"""Shared JetStream topology for typed catalogue work and dead letters."""

from atlas.platform.config import get_float, get_int
from nats.js.api import DiscardPolicy, RetentionPolicy, StorageType, StreamConfig
from atlas.platform.messaging.topology import ensure_stream_contract


WORK_STREAM = "ATLAS_CATALOGUE_WORK"
INGEST_SUBJECT = "atlas.catalogue.ingest"
MATERIALIZATION_MAINTENANCE_SUBJECT = "atlas.catalogue.materialization"
WORK_SUBJECTS = (INGEST_SUBJECT, MATERIALIZATION_MAINTENANCE_SUBJECT)

DEAD_LETTER_STREAM = "ATLAS_DEAD_LETTER"
INGEST_DEAD_LETTER_SUBJECT = "atlas.dead_letter.ingest"
DEAD_LETTER_SUBJECTS = (INGEST_DEAD_LETTER_SUBJECT,)


async def ensure_catalogue_work_stream(jetstream) -> None:
    replicas = get_int("ATLAS_CATALOGUE_WORK_STREAM_REPLICAS")
    config = StreamConfig(
        name=WORK_STREAM,
        subjects=list(WORK_SUBJECTS),
        retention=RetentionPolicy.WORK_QUEUE,
        storage=StorageType.FILE,
        num_replicas=replicas,
        max_age=0,
        max_bytes=get_int("ATLAS_CATALOGUE_WORK_MAX_BYTES"),
        discard=DiscardPolicy.NEW,
    )
    await ensure_stream_contract(jetstream, config)


async def ensure_dead_letter_stream(jetstream) -> None:
    config = StreamConfig(
        name=DEAD_LETTER_STREAM,
        subjects=list(DEAD_LETTER_SUBJECTS),
        retention=RetentionPolicy.LIMITS,
        storage=StorageType.FILE,
        num_replicas=get_int("ATLAS_CATALOGUE_WORK_STREAM_REPLICAS"),
        max_age=get_float("ATLAS_DEAD_LETTER_TTL_SECONDS"),
        max_bytes=get_int("ATLAS_DEAD_LETTER_MAX_BYTES"),
    )
    await ensure_stream_contract(jetstream, config)
