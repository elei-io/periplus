from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

import duckdb
import psycopg

from atlas.platform.catalogue.operations import (
    is_retryable_catalogue_unavailability,
    run_with_catalogue_retry,
)


class CatalogueOperationRetryTests(unittest.TestCase):
    def test_control_plane_outage_is_retryable_unavailability(self) -> None:
        self.assertTrue(
            is_retryable_catalogue_unavailability(
                psycopg.OperationalError("control plane unavailable")
            )
        )
        self.assertFalse(is_retryable_catalogue_unavailability(ValueError("bad row")))
        self.assertTrue(
            is_retryable_catalogue_unavailability(
                duckdb.IOException("metadata connection failed")
            )
        )

    def test_transaction_conflicts_retry_with_bounded_backoff(self) -> None:
        attempts = 0

        def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise duckdb.TransactionException("conflict")
            return "committed"

        with (
            patch(
                "atlas.platform.catalogue.operations.CATALOGUE_OPERATION_RETRY_INITIAL_SECONDS",
                0.1,
            ),
            patch(
                "atlas.platform.catalogue.operations.CATALOGUE_OPERATION_RETRY_MAX_SECONDS",
                0.25,
            ),
            patch("atlas.platform.catalogue.operations.time.sleep") as sleep,
        ):
            result = run_with_catalogue_retry(operation, description="test commit")

        self.assertEqual(result, "committed")
        self.assertEqual(sleep.call_args_list, [call(0.1), call(0.2)])

    def test_ducklake_compaction_conflict_retries_even_when_misclassified(
        self,
    ) -> None:
        operation = MagicMock(
            side_effect=[
                duckdb.InvalidInputException(
                    "Invalid Input Error: Failed to commit: Failed to commit "
                    "DuckLake transaction. Transaction conflict - attempting "
                    'to delete from table with index "430" - but another '
                    "transaction has compacted it"
                ),
                "committed",
            ]
        )
        with (
            patch(
                "atlas.platform.catalogue.operations.CATALOGUE_OPERATION_RETRY_INITIAL_SECONDS",
                0,
            ),
            patch(
                "atlas.platform.catalogue.operations.CATALOGUE_OPERATION_RETRY_MAX_SECONDS",
                0,
            ),
            patch("atlas.platform.catalogue.operations.time.sleep"),
        ):
            result = run_with_catalogue_retry(
                operation, description="materialization partition"
            )

        self.assertEqual(result, "committed")
        self.assertEqual(operation.call_count, 2)

    def test_io_unavailability_stays_live_past_conflict_limit(
        self,
    ) -> None:
        operation = MagicMock(
            side_effect=[
                duckdb.IOException("metadata unavailable"),
                duckdb.IOException("metadata still unavailable"),
                "committed",
            ]
        )
        with (
            patch(
                "atlas.platform.catalogue.operations.CATALOGUE_OPERATION_MAX_ATTEMPTS",
                1,
            ),
            patch(
                "atlas.platform.catalogue.operations.CATALOGUE_OPERATION_RETRY_INITIAL_SECONDS",
                0.1,
            ),
            patch(
                "atlas.platform.catalogue.operations.CATALOGUE_OPERATION_RETRY_MAX_SECONDS",
                0.25,
            ),
            patch("atlas.platform.catalogue.operations.time.sleep") as sleep,
        ):
            result = run_with_catalogue_retry(
                operation, description="materialization partition"
            )

        self.assertEqual(result, "committed")
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(0.1), call(0.2)])

    def test_transaction_conflict_retry_is_bounded(self) -> None:
        operation = MagicMock(
            side_effect=duckdb.TransactionException("still conflicting")
        )
        with (
            patch(
                "atlas.platform.catalogue.operations.CATALOGUE_OPERATION_MAX_ATTEMPTS",
                3,
            ),
            patch(
                "atlas.platform.catalogue.operations.CATALOGUE_OPERATION_RETRY_INITIAL_SECONDS",
                0,
            ),
            patch(
                "atlas.platform.catalogue.operations.CATALOGUE_OPERATION_RETRY_MAX_SECONDS",
                0,
            ),
            patch("atlas.platform.catalogue.operations.time.sleep"),
            self.assertRaises(duckdb.TransactionException),
        ):
            run_with_catalogue_retry(operation, description="test commit")
        self.assertEqual(operation.call_count, 3)


if __name__ == "__main__":
    unittest.main()
