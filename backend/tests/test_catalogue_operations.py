from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

import duckdb

from repository.catalogue.operations import (
    _MAINTENANCE_LOCK_KEY,
    advisory_lock_key,
    operation_locks,
    run_with_catalogue_retry,
)


class CatalogueOperationLockTests(unittest.TestCase):
    def _catalogue_config(self, name: str) -> str:
        return {
            "ATLAS_CATALOGUE_CATALOG": "postgres",
            "ATLAS_CATALOGUE_CATALOG_DSN": "postgresql://atlas",
        }[name]

    def test_microbatch_uses_one_postgres_session_for_every_lock(self) -> None:
        connection = MagicMock()
        connect = MagicMock()
        connect.return_value.__enter__.return_value = connection

        with (
            patch(
                "repository.catalogue.operations.get_str",
                side_effect=self._catalogue_config,
            ),
            patch(
                "repository.catalogue.operations.get_float",
                return_value=30.0,
            ),
            patch("repository.catalogue.operations.psycopg.connect", connect),
        ):
            with operation_locks(("second", "first", "first")):
                pass

        connect.assert_called_once_with("postgresql://atlas")
        first = advisory_lock_key("atlas-catalog-operation:first")
        second = advisory_lock_key("atlas-catalog-operation:second")
        self.assertEqual(
            connection.execute.call_args_list,
            [
                call(
                    "SELECT set_config('lock_timeout', %s, false)",
                    ("30000ms",),
                ),
                call(
                    "SELECT pg_advisory_lock_shared(%s)",
                    (_MAINTENANCE_LOCK_KEY,),
                ),
                call("SELECT pg_advisory_lock(%s)", (first,)),
                call("SELECT pg_advisory_lock(%s)", (second,)),
                call("SELECT pg_advisory_unlock(%s)", (second,)),
                call("SELECT pg_advisory_unlock(%s)", (first,)),
                call(
                    "SELECT pg_advisory_unlock_shared(%s)",
                    (_MAINTENANCE_LOCK_KEY,),
                ),
            ],
        )

    def test_partial_acquisition_releases_only_acquired_locks(self) -> None:
        connection = MagicMock()
        connect = MagicMock()
        connect.return_value.__enter__.return_value = connection
        first = advisory_lock_key("atlas-catalog-operation:first")
        second = advisory_lock_key("atlas-catalog-operation:second")

        def execute(query, parameters):
            if query == "SELECT pg_advisory_lock(%s)" and parameters == (second,):
                raise TimeoutError("lock timeout")

        connection.execute.side_effect = execute
        with (
            patch(
                "repository.catalogue.operations.get_str",
                side_effect=self._catalogue_config,
            ),
            patch(
                "repository.catalogue.operations.get_float",
                return_value=30.0,
            ),
            patch("repository.catalogue.operations.psycopg.connect", connect),
            self.assertRaisesRegex(TimeoutError, "lock timeout"),
        ):
            with operation_locks(("first", "second")):
                pass

        connection.execute.assert_any_call(
            "SELECT pg_advisory_unlock(%s)", (first,)
        )
        connection.execute.assert_any_call(
            "SELECT pg_advisory_unlock_shared(%s)", (_MAINTENANCE_LOCK_KEY,)
        )
        self.assertNotIn(
            call("SELECT pg_advisory_unlock(%s)", (second,)),
            connection.execute.call_args_list,
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
            patch("repository.catalogue.operations.get_int", return_value=5),
            patch(
                "repository.catalogue.operations.get_float",
                side_effect=lambda name: {
                    "ATLAS_CATALOG_OPERATION_RETRY_INITIAL_SECONDS": 0.1,
                    "ATLAS_CATALOG_OPERATION_RETRY_MAX_SECONDS": 0.25,
                }[name],
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
            patch("repository.catalogue.operations.get_int", return_value=3),
            patch("repository.catalogue.operations.get_float", return_value=0),
            patch("repository.catalogue.operations.time.sleep"),
            self.assertRaises(duckdb.TransactionException),
        ):
            run_with_catalogue_retry(operation, description="test commit")
        self.assertEqual(operation.call_count, 3)


if __name__ == "__main__":
    unittest.main()
