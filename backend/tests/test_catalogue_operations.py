from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch
from uuid import UUID

import duckdb
import psycopg

from repository.catalogue.operations import (
    _advisory_key,
    is_retryable_catalogue_unavailability,
    operation_locks,
    repository_commit_lock,
    run_with_catalogue_retry,
)


class CatalogueOperationLockTests(unittest.TestCase):
    def test_operation_locks_use_sorted_deduplicated_postgres_keys(self) -> None:
        with patch(
            "repository.catalogue.operations._advisory_locks"
        ) as locks:
            locks.return_value.__enter__.return_value = None
            with operation_locks(MagicMock(), ("second", "first", "first")):
                pass

        self.assertEqual(
            locks.call_args.args[0],
            [
                _advisory_key("operation", "first"),
                _advisory_key("operation", "second"),
            ],
        )

    def test_repository_batch_fences_each_identity(self) -> None:
        crawl_ids = [UUID(int=value) for value in range(1, 4)]
        content_ids = ["content-b", "content-a"]
        url_ids = ["url-b", "url-a"]
        with patch(
            "repository.catalogue.operations._advisory_locks"
        ) as locks:
            locks.return_value.__enter__.return_value = None
            with repository_commit_lock(
                MagicMock(),
                crawl_ids=crawl_ids,
                content_ids=content_ids,
                url_ids=url_ids,
            ):
                pass

        self.assertEqual(len(locks.call_args.args[0]), 7)

    def test_control_plane_outage_is_retryable_unavailability(self) -> None:
        self.assertTrue(
            is_retryable_catalogue_unavailability(
                psycopg.OperationalError("control plane unavailable")
            )
        )
        self.assertFalse(is_retryable_catalogue_unavailability(ValueError("bad row")))

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
                "repository.catalogue.operations.CATALOGUE_OPERATION_RETRY_INITIAL_SECONDS",
                0.1,
            ),
            patch(
                "repository.catalogue.operations.CATALOGUE_OPERATION_RETRY_MAX_SECONDS",
                0.25,
            ),
            patch("repository.catalogue.operations.time.sleep") as sleep,
        ):
            result = run_with_catalogue_retry(operation, description="test commit")

        self.assertEqual(result, "committed")
        self.assertEqual(sleep.call_args_list, [call(0.1), call(0.2)])

    def test_transaction_conflict_retry_is_bounded(self) -> None:
        operation = MagicMock(
            side_effect=duckdb.TransactionException("still conflicting")
        )
        with (
            patch(
                "repository.catalogue.operations.CATALOGUE_OPERATION_MAX_ATTEMPTS",
                3,
            ),
            patch(
                "repository.catalogue.operations.CATALOGUE_OPERATION_RETRY_INITIAL_SECONDS",
                0,
            ),
            patch(
                "repository.catalogue.operations.CATALOGUE_OPERATION_RETRY_MAX_SECONDS",
                0,
            ),
            patch("repository.catalogue.operations.time.sleep"),
            self.assertRaises(duckdb.TransactionException),
        ):
            run_with_catalogue_retry(operation, description="test commit")
        self.assertEqual(operation.call_count, 3)


if __name__ == "__main__":
    unittest.main()
