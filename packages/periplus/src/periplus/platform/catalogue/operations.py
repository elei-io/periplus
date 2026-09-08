"""Bounded retry classification for deterministic DuckLake operations."""

from __future__ import annotations

from collections.abc import Callable
import logging
import time
from typing import TypeVar

import duckdb
import psycopg
from sqlalchemy.exc import OperationalError as SqlAlchemyOperationalError
from periplus.platform.catalogue.exceptions import CatalogueOutcomePending

from periplus.platform.config.performance import (
    CATALOGUE_OPERATION_MAX_ATTEMPTS,
    CATALOGUE_OPERATION_RETRY_INITIAL_SECONDS,
    CATALOGUE_OPERATION_RETRY_MAX_SECONDS,
)

_T = TypeVar("_T")


def is_catalogue_data_corruption(exc: BaseException) -> bool:
    """Return whether DuckDB found durable data that cannot be read."""

    if not isinstance(exc, duckdb.Error):
        return False
    message = str(exc).lower()
    parquet = ".parquet" in message or "parquet" in message
    missing = any(
        marker in message
        for marker in (
            "no such file or directory",
            "file does not exist",
            "http 404",
        )
    )
    invalid = any(
        marker in message
        for marker in (
            "checksum mismatch",
            "corrupt",
            "invalid parquet",
            "not a parquet file",
            "parquet magic bytes not found",
        )
    )
    return parquet and (missing or invalid)


def is_retryable_catalogue_unavailability(exc: BaseException) -> bool:
    """Return whether durable work must remain live across this failure."""

    from periplus.retention.identities import WriteClaimUnavailable

    return not is_catalogue_data_corruption(exc) and isinstance(
        exc,
        (duckdb.IOException, psycopg.OperationalError, SqlAlchemyOperationalError,
         CatalogueOutcomePending, WriteClaimUnavailable),
    )


def is_retryable_catalogue_transaction_conflict(exc: BaseException) -> bool:
    """Return whether DuckLake rejected a transaction due to concurrent work."""

    from periplus.retention.identities import WriteClaimUnavailable

    if isinstance(exc, (duckdb.TransactionException, WriteClaimUnavailable)):
        return True
    if not isinstance(exc, duckdb.Error):
        return False
    message = str(exc).lower()
    return (
        "transaction conflict" in message
        and "another transaction has compacted it" in message
    )


def run_with_catalogue_retry(
    operation: Callable[[], _T],
    *,
    description: str,
    on_conflict: Callable[[], None] | None = None,
) -> _T:
    """Bound local retries so durable delivery can resume after failure."""

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
                if unavailability_failures >= maximum_attempts:
                    raise
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
            if on_conflict is not None:
                on_conflict()
            conflict_attempt += 1
            logging.debug(
                "%s conflicted; retrying attempt %d/%d in %.3fs",
                description,
                conflict_attempt,
                maximum_attempts,
                delay,
                exc_info=True,
            )
            time.sleep(delay)
            delay = min(maximum_delay, max(delay * 2, 0.001))
