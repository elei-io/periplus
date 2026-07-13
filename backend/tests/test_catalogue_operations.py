from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

from repository.catalogue.operations import (
    _MAINTENANCE_LOCK_KEY,
    advisory_lock_key,
    operation_locks,
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


if __name__ == "__main__":
    unittest.main()
