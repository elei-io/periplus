"""Protect content between object publication and durable evidence commit.

A publisher writes its claim before checking the retirement marker. A reclaimer
writes the marker before checking claims. With strongly consistent object reads
and listings, either the publisher is protected or it refuses the retiring object.
No process lock or open database transaction spans object I/O.
"""
from io import BytesIO
from datetime import UTC, datetime, timedelta
from uuid import UUID

from periplus.ingestion.objects.store import ObjectStore

PREFIX = "runtime/publications/"
RETIRING_PREFIX = "runtime/retiring-content/"


class ContentRetiring(RuntimeError):
    """Retry publication after an exact content object's retirement finishes."""


def claim_key(content_hash: str, visit_id: UUID) -> str:
    return f"{PREFIX}{content_hash}/{visit_id}"


def claim(store: ObjectStore, content_hash: str, visit_id: UUID) -> None:
    key = claim_key(content_hash, visit_id)
    store.put_if_absent(key, BytesIO(b"publication"))
    if store.exists(RETIRING_PREFIX + content_hash):
        store.delete(key)
        raise ContentRetiring("content retirement in progress; retry publication")


def release(store: ObjectStore, content_hash: str, visit_id: UUID) -> None:
    store.delete(claim_key(content_hash, visit_id))


def begin_reclamation(store: ObjectStore, content_hash: str, *, now: datetime | None = None) -> bool:
    # Exact-content operation leases serialize reclaimers. After a crashed owner,
    # leave its marker in place beyond the worker's 300s hard timeout before a
    # replacement may finish the deletion and admit publishers again.
    key = RETIRING_PREFIX + content_hash
    created = store.put_if_absent(key, BytesIO(datetime.now(UTC).isoformat().encode()))
    if not created:
        with store.open(key) as source:
            timestamp = source.read(128)
        try:
            started_at = datetime.fromisoformat(timestamp.decode())
        except (ValueError, UnicodeError):
            return False
        if started_at.utcoffset() is None or started_at > (now or datetime.now(UTC)) - timedelta(hours=1):
            return False
    if next(iter(store.list_objects(PREFIX + content_hash + "/")), None) is not None:
        store.delete(RETIRING_PREFIX + content_hash)
        return False
    return True


def end_reclamation(store: ObjectStore, content_hash: str) -> None:
    store.delete(RETIRING_PREFIX + content_hash)
