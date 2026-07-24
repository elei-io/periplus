"""Bounded retry classification for deterministic DuckLake operations."""

from __future__ import annotations

from collections.abc import Callable
import logging
import time
from typing import TypeVar

import duckdb
import psycopg

from config.performance import (
    CATALOGUE_OPERATION_MAX_ATTEMPTS,
    CATALOGUE_OPERATION_RETRY_INITIAL_SECONDS,
    CATALOGUE_OPERATION_RETRY_MAX_SECONDS,
)

_T = TypeVar("_T")


def is_retryable_catalogue_unavailability(exc: BaseException) -> bool:
    """Return whether durable work must remain live across this failure."""

    return isinstance(exc, psycopg.OperationalError)


def run_with_catalogue_retry(
    operation: Callable[[], _T], *, description: str
) -> _T:
    """Retry only typed catalogue transaction conflicts with bounded backoff."""

    attempts = CATALOGUE_OPERATION_MAX_ATTEMPTS
    delay = CATALOGUE_OPERATION_RETRY_INITIAL_SECONDS
    maximum_delay = CATALOGUE_OPERATION_RETRY_MAX_SECONDS
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
