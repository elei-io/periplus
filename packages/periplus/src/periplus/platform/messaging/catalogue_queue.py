"""Shared JetStream topology for typed catalogue work and dead letters."""

from periplus.platform.config import get_float, get_int
from nats.js.api import DiscardPolicy, RetentionPolicy, StorageType, StreamConfig
from periplus.platform.messaging.topology import ensure_stream_contract


WORK_STREAM = "PERIPLUS_CATALOGUE_WORK"
INGEST_SUBJECT = "periplus.catalogue.ingest"
MATERIALIZATION_PLAN_SUBJECT = "periplus.catalogue.materialization.plan"
MATERIALIZATION_BATCH_SUBJECT = "periplus.catalogue.materialization.batch"
MATERIALIZATION_ACTIVATE_SUBJECT = "periplus.catalogue.materialization.activate"
WORK_SUBJECTS = (
    INGEST_SUBJECT,
    MATERIALIZATION_PLAN_SUBJECT,
    MATERIALIZATION_BATCH_SUBJECT,
    MATERIALIZATION_ACTIVATE_SUBJECT,
)

DEAD_LETTER_STREAM = "PERIPLUS_DEAD_LETTER"
INGEST_DEAD_LETTER_SUBJECT = "periplus.dead_letter.ingest"
DEAD_LETTER_SUBJECTS = (INGEST_DEAD_LETTER_SUBJECT,)


async def ensure_catalogue_work_stream(jetstream) -> None:
    replicas = get_int("PERIPLUS_CATALOGUE_WORK_STREAM_REPLICAS")
    config = StreamConfig(
        name=WORK_STREAM,
        subjects=list(WORK_SUBJECTS),
        retention=RetentionPolicy.WORK_QUEUE,
        storage=StorageType.FILE,
        num_replicas=replicas,
        max_age=0,
        max_bytes=get_int("PERIPLUS_CATALOGUE_WORK_MAX_BYTES"),
        discard=DiscardPolicy.NEW,
    )
    await ensure_stream_contract(jetstream, config)


async def ensure_dead_letter_stream(jetstream) -> None:
    config = StreamConfig(
        name=DEAD_LETTER_STREAM,
        subjects=list(DEAD_LETTER_SUBJECTS),
        retention=RetentionPolicy.LIMITS,
        storage=StorageType.FILE,
        num_replicas=get_int("PERIPLUS_CATALOGUE_WORK_STREAM_REPLICAS"),
        max_age=get_float("PERIPLUS_DEAD_LETTER_TTL_SECONDS"),
        max_bytes=get_int("PERIPLUS_DEAD_LETTER_MAX_BYTES"),
    )
    await ensure_stream_contract(jetstream, config)
