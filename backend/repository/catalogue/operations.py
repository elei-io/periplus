"""Cooperative fencing for deterministic DuckLake write operations."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
import logging
import time
from typing import Callable, TypeVar
from uuid import UUID

import duckdb
from ducklake_client import DuckLakeFenceError, FenceSpec
import psycopg

from config import get_float, get_int
from repository.catalogue.client import Catalogue

_T = TypeVar("_T")
_MAINTENANCE_IDENTITY = "catalogue-maintenance"


@contextmanager
def operation_lock(catalogue: Catalogue, operation_id: str) -> Iterator[None]:
    """Fence identity resolution and commit across DuckLake writer processes."""

    with operation_locks(catalogue, (operation_id,)):
        yield


@contextmanager
def operation_locks(
    catalogue: Catalogue, operation_ids: Iterable[str]
) -> Iterator[None]:
    """Fence independent mutation identities through one catalogue session."""

    fences = [
        FenceSpec.shared(_MAINTENANCE_IDENTITY),
        *(
            FenceSpec.exclusive("operation", operation_id)
            for operation_id in sorted(set(operation_ids))
        ),
    ]
    with catalogue.lake.fence_set(
        *fences,
        namespace="atlas",
        timeout=get_float("ATLAS_CATALOG_OPERATION_LOCK_TIMEOUT_SECONDS"),
    ):
        yield


@contextmanager
def repository_commit_lock(
    catalogue: Catalogue,
    *,
    crawl_ids: Sequence[UUID],
    content_ids: Sequence[str],
) -> Iterator[None]:
    """Fence every independently overlapping identity in one repository batch."""

    fences = [
        FenceSpec.shared(_MAINTENANCE_IDENTITY),
        *(
            FenceSpec.exclusive("crawl", str(crawl_id))
            for crawl_id in sorted(set(crawl_ids), key=str)
        ),
        *(
            FenceSpec.exclusive("content", content_id)
            for content_id in sorted(set(content_ids))
        ),
    ]
    with catalogue.lake.fence_set(
        *fences,
        namespace="atlas",
        timeout=get_float("ATLAS_CATALOG_OPERATION_LOCK_TIMEOUT_SECONDS"),
    ):
        yield


@contextmanager
def maintenance_lock(catalogue: Catalogue) -> Iterator[None]:
    """Wait for in-flight commits and exclude new commits during maintenance."""

    with catalogue.lake.fence_set(
        FenceSpec.exclusive(_MAINTENANCE_IDENTITY),
        namespace="atlas",
        timeout=get_float("ATLAS_CATALOG_OPERATION_LOCK_TIMEOUT_SECONDS"),
    ):
        yield


def is_retryable_catalogue_unavailability(exc: BaseException) -> bool:
    """Return whether durable work must remain live across this failure."""

    return isinstance(exc, (DuckLakeFenceError, psycopg.OperationalError))


def run_with_catalogue_retry(
    operation: Callable[[], _T], *, description: str
) -> _T:
    """Retry only typed catalogue transaction conflicts with bounded backoff."""

    attempts = get_int("ATLAS_CATALOG_OPERATION_MAX_ATTEMPTS")
    delay = get_float("ATLAS_CATALOG_OPERATION_RETRY_INITIAL_SECONDS")
    maximum_delay = get_float("ATLAS_CATALOG_OPERATION_RETRY_MAX_SECONDS")
    if attempts <= 0 or delay < 0 or maximum_delay < 0:
        raise ValueError("catalogue operation retry settings are invalid")
    retryable = (
        duckdb.TransactionException,
        psycopg.errors.DeadlockDetected,
        psycopg.errors.LockNotAvailable,
        psycopg.errors.SerializationFailure,
    )
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except retryable:
            if attempt >= attempts:
                raise
            logging.warning(
                "%s conflicted; retrying attempt %d/%d in %.3fs",
                description,
                attempt + 1,
                attempts,
                delay,
                exc_info=True,
            )
            time.sleep(delay)
            delay = min(maximum_delay, max(delay * 2, 0.001))
    raise AssertionError("unreachable")
