from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch
from uuid import UUID

import duckdb
from ducklake_client import DuckLakeFenceError, FenceSpec

from repository.catalogue.operations import (
    is_retryable_catalogue_unavailability,
    operation_locks,
    repository_commit_lock,
    run_with_catalogue_retry,
)


class CatalogueOperationLockTests(unittest.TestCase):
    def _catalogue(self) -> MagicMock:
        catalogue = MagicMock()
        catalogue.lake.fence_set.return_value.__enter__.return_value = catalogue.lake
        return catalogue

    def test_operation_locks_are_one_independent_fence_set(self) -> None:
        catalogue = self._catalogue()

        with patch("repository.catalogue.operations.CATALOGUE_OPERATION_LOCK_TIMEOUT_SECONDS", 30.0):
            with operation_locks(catalogue, ("second", "first", "first")):
                pass

        catalogue.lake.fence_set.assert_called_once_with(
            FenceSpec.exclusive("operation", "first"),
            FenceSpec.exclusive("operation", "second"),
            namespace="atlas",
            timeout=30.0,
        )

    def test_repository_batch_fences_each_crawl_content_and_url_identity(self) -> None:
        catalogue = self._catalogue()
        crawl_ids = [UUID(int=value) for value in range(1, 101)]
        content_ids = [f"sha256:{value:064x}" for value in range(100)]
        url_ids = [f"{value:064x}" for value in range(100)]

        with patch("repository.catalogue.operations.CATALOGUE_OPERATION_LOCK_TIMEOUT_SECONDS", 30.0):
            with repository_commit_lock(
                catalogue,
                crawl_ids=crawl_ids,
                content_ids=content_ids,
                url_ids=url_ids,
            ):
                pass

        catalogue.lake.fence_set.assert_called_once()
        args = catalogue.lake.fence_set.call_args.args
        self.assertEqual(len(args), 300)
        self.assertEqual(
            {spec.keys for spec in args[:100]},
            {("crawl", str(crawl_id)) for crawl_id in crawl_ids},
        )
        self.assertEqual(
            {spec.keys for spec in args[100:]},
            {
                ("content", content_id)
                for content_id in content_ids
            }
            | {
                ("url", url_id)
                for url_id in url_ids
            },
        )
        self.assertEqual(
            {spec.keys for spec in args[100:200]},
            {("content", content_id) for content_id in content_ids},
        )
        self.assertEqual(
            {spec.keys for spec in args[200:]},
            {("url", url_id) for url_id in url_ids},
        )

    def test_fence_failure_is_retryable_infrastructure_unavailability(self) -> None:
        self.assertTrue(
            is_retryable_catalogue_unavailability(
                DuckLakeFenceError("catalogue unavailable")
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
        self.assertEqual(attempts, 3)
        self.assertEqual(sleep.call_args_list, [call(0.1), call(0.2)])

    def test_transaction_conflict_retry_is_bounded(self) -> None:
        operation = MagicMock(
            side_effect=duckdb.TransactionException("still conflicting")
        )
        with (
            patch("repository.catalogue.operations.CATALOGUE_OPERATION_MAX_ATTEMPTS", 3),
            patch("repository.catalogue.operations.CATALOGUE_OPERATION_RETRY_INITIAL_SECONDS", 0),
            patch("repository.catalogue.operations.CATALOGUE_OPERATION_RETRY_MAX_SECONDS", 0),
            patch("repository.catalogue.operations.time.sleep"),
            self.assertRaises(duckdb.TransactionException),
        ):
            run_with_catalogue_retry(operation, description="test commit")
        self.assertEqual(operation.call_count, 3)


if __name__ == "__main__":
    unittest.main()
