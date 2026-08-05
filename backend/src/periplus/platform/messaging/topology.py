"""Shared exact-contract reconciliation for Periplus JetStream resources."""

from nats.js.api import StorageType
from nats.js.errors import BadRequestError, NoKeysError, NotFoundError


async def list_kv_keys(bucket) -> list[str]:
    """List current KV keys without retaining a watcher consumer."""

    if not hasattr(bucket, "watchall"):
        try:
            return await bucket.keys()
        except NoKeysError:
            return []
    watcher = await bucket.watchall(ignore_deletes=True, meta_only=True)
    consumer_name: str | None = None
    try:
        info = await watcher._sub.consumer_info()
        consumer_name = info.name
        keys: list[str] = []
        async for entry in watcher:
            if entry is None:
                break
            keys.append(entry.key)
        return keys
    finally:
        await watcher.stop()
        if consumer_name is not None:
            try:
                await bucket._js.delete_consumer(bucket._stream, consumer_name)
            except NotFoundError:
                pass


async def validate_kv_contract(
    bucket,
    *,
    name: str,
    ttl: float,
    max_bytes: int,
    replicas: int,
) -> None:
    """Validate the common exact contract for Periplus file-backed KV buckets."""

    config = (await bucket.status()).stream_info.config
    mismatches: list[str] = []
    if config.storage != StorageType.FILE:
        mismatches.append("file storage")
    if config.max_msgs_per_subject != 1:
        mismatches.append("history=1")
    if config.max_age != ttl:
        mismatches.append(f"ttl={ttl:g}s")
    if config.max_bytes != max_bytes:
        mismatches.append(f"max_bytes={max_bytes}")
    if config.num_replicas != replicas:
        mismatches.append(f"replicas={replicas}")
    if mismatches:
        raise RuntimeError(
            f"JetStream KV {name} must use " + ", ".join(mismatches)
        )


async def ensure_stream_contract(jetstream, expected) -> None:
    """Create one stream race-safely, then validate its complete contract."""

    try:
        info = await jetstream.stream_info(expected.name)
    except NotFoundError:
        try:
            await jetstream.add_stream(config=expected)
        except BadRequestError:
            info = await jetstream.stream_info(expected.name)
        else:
            return

    actual = info.config
    mismatches: list[str] = []
    if set(actual.subjects) != set(expected.subjects):
        mismatches.append(f"subjects={list(expected.subjects)}")
    if actual.retention != expected.retention:
        mismatches.append(f"retention={expected.retention.value}")
    if actual.storage != expected.storage:
        mismatches.append(f"storage={expected.storage.value}")
    if actual.num_replicas != expected.num_replicas:
        mismatches.append(f"replicas={expected.num_replicas}")
    if actual.max_age != expected.max_age:
        mismatches.append(f"max_age={expected.max_age:g}s")
    if actual.max_bytes != expected.max_bytes:
        mismatches.append(f"max_bytes={expected.max_bytes}")
    if actual.discard != expected.discard:
        mismatches.append(f"discard={expected.discard.value}")
    if mismatches:
        raise RuntimeError(
            f"JetStream {expected.name} must use " + ", ".join(mismatches)
        )
