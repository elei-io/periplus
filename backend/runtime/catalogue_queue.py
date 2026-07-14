"""Shared JetStream topology for typed catalogue work and dead letters."""

from config import get_float, get_int
from nats.js.api import RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import NotFoundError


WORK_STREAM = "ATLAS_CATALOGUE_WORK"
INGEST_SUBJECT = "atlas.catalogue.ingest"
MATERIALIZE_LIVE_SUBJECT = "atlas.catalogue.materialize.live"
MATERIALIZE_BACKFILL_SUBJECT = "atlas.catalogue.materialize.backfill"
WORK_SUBJECTS = (
    INGEST_SUBJECT,
    MATERIALIZE_LIVE_SUBJECT,
    MATERIALIZE_BACKFILL_SUBJECT,
)

DEAD_LETTER_STREAM = "ATLAS_DEAD_LETTER"
INGEST_DEAD_LETTER_SUBJECT = "atlas.dead_letter.ingest"
MATERIALIZE_DEAD_LETTER_SUBJECT = "atlas.dead_letter.materialize"
DEAD_LETTER_SUBJECTS = (
    INGEST_DEAD_LETTER_SUBJECT,
    MATERIALIZE_DEAD_LETTER_SUBJECT,
)


async def ensure_catalogue_work_stream(jetstream) -> None:
    replicas = get_int("ATLAS_CATALOGUE_WORK_STREAM_REPLICAS")
    config = StreamConfig(
        name=WORK_STREAM,
        subjects=list(WORK_SUBJECTS),
        retention=RetentionPolicy.WORK_QUEUE,
        storage=StorageType.FILE,
        num_replicas=replicas,
        max_age=0,
        max_bytes=-1,
    )
    await _ensure_stream(jetstream, config)


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
    await _ensure_stream(jetstream, config)


async def _ensure_stream(jetstream, expected: StreamConfig) -> None:
    try:
        info = await jetstream.stream_info(expected.name)
    except NotFoundError:
        await jetstream.add_stream(config=expected)
        return
    actual = info.config
    mismatches: list[str] = []
    if set(actual.subjects) != set(expected.subjects):
        mismatches.append(f"subjects={list(expected.subjects)}")
    if actual.retention != expected.retention:
        mismatches.append(f"retention={expected.retention.value}")
    if actual.storage != StorageType.FILE:
        mismatches.append("file storage")
    if actual.num_replicas != expected.num_replicas:
        mismatches.append(f"replicas={expected.num_replicas}")
    if actual.max_age != expected.max_age:
        mismatches.append(f"max_age={expected.max_age:g}s")
    if actual.max_bytes != expected.max_bytes:
        mismatches.append(f"max_bytes={expected.max_bytes}")
    if mismatches:
        raise RuntimeError(
            f"JetStream {expected.name} must use " + ", ".join(mismatches)
        )
