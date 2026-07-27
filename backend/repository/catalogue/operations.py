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
from repository.catalogue.duckbasin import DuckBasinUnavailableError

_T = TypeVar("_T")


def is_retryable_catalogue_unavailability(exc: BaseException) -> bool:
    """Return whether durable work must remain live across this failure."""

    return isinstance(
        exc,
        (DuckBasinUnavailableError, psycopg.OperationalError),
    )


def is_retryable_catalogue_transaction_conflict(exc: BaseException) -> bool:
    """Return whether DuckLake rejected a transaction due to concurrent work."""

    if isinstance(exc, duckdb.TransactionException):
        return True
    if not isinstance(exc, duckdb.Error):
        return False
    message = str(exc).lower()
    return (
        "transaction conflict" in message
        and "another transaction has compacted it" in message
    )


def run_with_catalogue_retry(
    operation: Callable[[], _T], *, description: str
) -> _T:
    """Keep availability failures live; bound transaction conflict retries."""

    maximum_attempts = CATALOGUE_OPERATION_MAX_ATTEMPTS
    delay = CATALOGUE_OPERATION_RETRY_INITIAL_SECONDS
    maximum_delay = CATALOGUE_OPERATION_RETRY_MAX_SECONDS
    if maximum_attempts <= 0 or delay < 0 or maximum_delay < 0:
        raise ValueError("catalogue operation retry settings are invalid")
    retryable_control_errors = (
        psycopg.errors.DeadlockDetected,
        psycopg.errors.LockNotAvailable,
        psycopg.errors.SerializationFailure,
    )
    conflict_attempt = 1
    unavailability_failures = 0
    while True:
        try:
            result = operation()
            if unavailability_failures:
                logging.info(
                    "%s recovered after %d unavailable attempts",
                    description,
                    unavailability_failures,
                )
            return result
        except Exception as exc:
            if is_retryable_catalogue_unavailability(exc):
                unavailability_failures += 1
                if (
                    unavailability_failures == 1
                    or unavailability_failures % 10 == 0
                ):
                    logging.warning(
                        "%s unavailable; retrying attempt %d in %.3fs",
                        description,
                        unavailability_failures + 1,
                        delay,
                        exc_info=unavailability_failures == 1,
                    )
                time.sleep(delay)
                delay = min(maximum_delay, max(delay * 2, 0.001))
                continue
            retryable_conflict = (
                isinstance(exc, retryable_control_errors)
                or is_retryable_catalogue_transaction_conflict(exc)
            )
            if not retryable_conflict:
                raise
            if conflict_attempt >= maximum_attempts:
                raise
            conflict_attempt += 1
            logging.warning(
                "%s conflicted; retrying attempt %d/%d in %.3fs",
                description,
                conflict_attempt,
                maximum_attempts,
                delay,
                exc_info=True,
            )
            time.sleep(delay)
            delay = min(maximum_delay, max(delay * 2, 0.001))
