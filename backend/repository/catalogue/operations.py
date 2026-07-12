"""Postgres advisory fencing for deterministic DuckLake write operations."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager
from hashlib import sha256

import psycopg

from config import get_float, get_str

_MAINTENANCE_LOCK_KEY: int


def advisory_lock_key(operation_id: str) -> int:
    """Map an operation identity to PostgreSQL's signed 64-bit lock space."""

    raw = sha256(operation_id.encode()).digest()[:8]
    return int.from_bytes(raw, byteorder="big", signed=True)


_MAINTENANCE_LOCK_KEY = advisory_lock_key("atlas-catalog-maintenance")


@contextmanager
def operation_lock(operation_id: str) -> Iterator[None]:
    """Fence identity resolution and commit across catalog-worker processes."""

    if get_str("ATLAS_CATALOGUE_CATALOG").lower() != "postgres":
        yield
        return
    key = advisory_lock_key(f"atlas-catalog-operation:{operation_id}")
    with psycopg.connect(get_str("ATLAS_CATALOGUE_CATALOG_DSN")) as connection:
        timeout_ms = int(
            get_float("ATLAS_CATALOG_OPERATION_LOCK_TIMEOUT_SECONDS") * 1000
        )
        connection.execute(
            "SELECT set_config('lock_timeout', %s, false)",
            (f"{timeout_ms}ms",),
        )
        connection.execute(
            "SELECT pg_advisory_lock_shared(%s)", (_MAINTENANCE_LOCK_KEY,)
        )
        connection.execute("SELECT pg_advisory_lock(%s)", (key,))
        try:
            yield
        finally:
            connection.execute("SELECT pg_advisory_unlock(%s)", (key,))
            connection.execute(
                "SELECT pg_advisory_unlock_shared(%s)", (_MAINTENANCE_LOCK_KEY,)
            )


@contextmanager
def operation_locks(operation_ids: Iterable[str]) -> Iterator[None]:
    """Acquire a stable lock order for one ingestion microbatch."""

    with ExitStack() as stack:
        for operation_id in sorted(set(operation_ids)):
            stack.enter_context(operation_lock(operation_id))
        yield


@contextmanager
def maintenance_lock() -> Iterator[None]:
    """Wait for in-flight commits and exclude new commits during maintenance."""

    if get_str("ATLAS_CATALOGUE_CATALOG").lower() != "postgres":
        yield
        return
    with psycopg.connect(get_str("ATLAS_CATALOGUE_CATALOG_DSN")) as connection:
        connection.execute(
            "SELECT pg_advisory_lock(%s)", (_MAINTENANCE_LOCK_KEY,)
        )
        try:
            yield
        finally:
            connection.execute(
                "SELECT pg_advisory_unlock(%s)", (_MAINTENANCE_LOCK_KEY,)
            )
