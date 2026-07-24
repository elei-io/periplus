from __future__ import annotations

from types import SimpleNamespace
import unittest

import duckdb

from repository.catalogue.client import Catalogue
from repository.catalogue.config import CatalogueConfig, catalogue_config_from_env


class _Cursor:
    description = [("value",)]

    def __init__(self, rows: list[tuple] | None = None) -> None:
        self._rows = rows or []

    def fetchall(self) -> list[tuple]:
        return self._rows


class _Connection:
    def __init__(self, *, failure_once: str | None = None) -> None:
        self.calls: list[tuple[str, object | None]] = []
        self.closed = False
        self.failure_once = failure_once

    def execute(self, sql: str, parameters=None) -> _Cursor:
        self.calls.append((sql, parameters))
        if self.failure_once is not None:
            message = self.failure_once
            self.failure_once = None
            raise duckdb.InvalidInputException(message)
        return _Cursor()

    def close(self) -> None:
        self.closed = True


class _Minter:
    def __init__(self) -> None:
        self.closed = False
        self.minted: list[SimpleNamespace] = []
        self.current_token_generation = 1
        self.invalidated_generations: list[int] = []

    def mint(self, *, duckdb_config=None) -> SimpleNamespace:
        connection = _Connection()
        minted = SimpleNamespace(
            connection=connection,
            session_id=f"{len(self.minted) + 1:016x}",
            lake_slug="atlas",
            catalogue_alias="atlas",
            token_generation=self.current_token_generation,
            close=connection.close,
        )
        self.minted.append(minted)
        return minted

    def connection_credentials_stale(self, minted: SimpleNamespace) -> bool:
        return minted.token_generation != self.current_token_generation

    def invalidate_connection_credentials(
        self, minted: SimpleNamespace
    ) -> None:
        self.invalidated_generations.append(minted.token_generation)
        if minted.token_generation == self.current_token_generation:
            self.current_token_generation += 1

    def close(self) -> None:
        self.closed = True


def _catalogue() -> tuple[Catalogue, _Connection, _Minter]:
    connection = _Connection()
    minter = _Minter()
    minted = SimpleNamespace(
        connection=connection,
        session_id="0123456789abcdef",
        lake_slug="atlas",
        catalogue_alias="atlas",
        token_generation=1,
        close=connection.close,
    )
    catalogue = Catalogue(
        CatalogueConfig(alias="atlas"),
        minted=minted,
        minter=minter,
    )
    connection.calls.clear()
    return catalogue, connection, minter


class ManagedCatalogueClientTests(unittest.TestCase):
    def test_minted_catalogue_alias_is_authoritative(self) -> None:
        config = catalogue_config_from_env(alias="basin_catalogue")

        self.assertEqual(config.alias, "basin_catalogue")

    def test_runtime_can_decode_duckdb_timestamptz_values(self) -> None:
        with duckdb.connect() as connection:
            value = connection.execute(
                "SELECT TIMESTAMPTZ '2026-07-23 00:00:00+00'"
            ).fetchone()[0]

        self.assertIsNotNone(value.tzinfo)

    def test_remote_transaction_uses_one_session_for_begin_work_and_commit(self) -> None:
        catalogue, connection, _minter = _catalogue()

        with catalogue.transaction():
            catalogue.remote_execute("INSERT INTO main.events VALUES (1)")

        self.assertEqual(
            connection.calls,
            [
                (
                    "CALL quack_query_by_name(current_catalog(), ?)",
                    ["BEGIN TRANSACTION"],
                ),
                (
                    "CALL quack_query_by_name(current_catalog(), ?)",
                    ["INSERT INTO main.events VALUES (1)"],
                ),
                (
                    "CALL quack_query_by_name(current_catalog(), ?)",
                    ["COMMIT"],
                ),
            ],
        )

    def test_remote_transaction_rolls_back_the_same_session(self) -> None:
        catalogue, connection, _minter = _catalogue()

        with self.assertRaisesRegex(RuntimeError, "stop"):
            with catalogue.transaction():
                raise RuntimeError("stop")

        self.assertEqual(
            [parameters for _sql, parameters in connection.calls],
            [["BEGIN TRANSACTION"], ["ROLLBACK"]],
        )

    def test_remote_rows_rejects_parameters_before_execution(self) -> None:
        catalogue, connection, _minter = _catalogue()

        with self.assertRaisesRegex(ValueError, "already-bound"):
            catalogue.remote_rows("SELECT $value", {"value": 1})

        self.assertEqual(connection.calls, [])

    def test_close_owns_the_connection_and_minter(self) -> None:
        catalogue, connection, minter = _catalogue()

        catalogue.close()

        self.assertTrue(connection.closed)
        self.assertTrue(minter.closed)

    def test_expired_quack_connection_is_reminted_and_retried(self) -> None:
        connection = _Connection()
        minter = _Minter()
        minted = SimpleNamespace(
            connection=connection,
            session_id="0123456789abcdef",
            lake_slug="atlas",
            catalogue_alias="atlas",
            token_generation=1,
            close=connection.close,
        )
        catalogue = Catalogue(
            CatalogueConfig(alias="atlas"),
            minted=minted,
            minter=minter,
            duckdb_config={"memory_limit": "1GB"},
        )
        connection.failure_once = "Invalid connection id"

        rows = catalogue.remote_rows("SELECT 1")

        self.assertEqual(rows, [])
        self.assertTrue(connection.closed)
        self.assertEqual(len(minter.minted), 1)
        self.assertEqual(catalogue.session_id, "0000000000000001")
        replacement = minter.minted[0].connection
        self.assertIn(
            (
                "FROM quack_query_by_name(current_catalog(), ?)",
                ["SELECT 1"],
            ),
            replacement.calls,
        )

    def test_direct_connection_queries_also_remint(self) -> None:
        connection = _Connection()
        minter = _Minter()
        minted = SimpleNamespace(
            connection=connection,
            session_id="0123456789abcdef",
            lake_slug="atlas",
            catalogue_alias="atlas",
            token_generation=1,
            close=connection.close,
        )
        catalogue = Catalogue(
            CatalogueConfig(alias="atlas"),
            minted=minted,
            minter=minter,
        )
        connection.failure_once = "Invalid connection id"

        catalogue.connection.execute(
            "SELECT * FROM atlas.main.documents WHERE document_id = $id",
            {"id": "document"},
        )

        self.assertTrue(connection.closed)
        self.assertEqual(len(minter.minted), 1)
        self.assertIn(
            (
                "SELECT * FROM atlas.main.documents WHERE document_id = $id",
                {"id": "document"},
            ),
            minter.minted[0].connection.calls,
        )

    def test_stale_token_generation_is_reminted_before_query(self) -> None:
        catalogue, connection, minter = _catalogue()
        minter.current_token_generation = 2

        catalogue.remote_rows("SELECT 1")

        self.assertTrue(connection.closed)
        self.assertEqual(len(minter.minted), 1)
        self.assertEqual(minter.minted[0].token_generation, 2)
        self.assertNotIn(
            (
                "FROM quack_query_by_name(current_catalog(), ?)",
                ["SELECT 1"],
            ),
            connection.calls,
        )

    def test_authorization_failure_invalidates_token_and_retries_once(self) -> None:
        catalogue, connection, minter = _catalogue()
        connection.failure_once = "Authorization failed"

        catalogue.remote_rows("SELECT 1")

        self.assertEqual(minter.invalidated_generations, [1])
        self.assertEqual(minter.current_token_generation, 2)
        self.assertTrue(connection.closed)
        self.assertEqual(len(minter.minted), 1)

    def test_authorization_failure_never_switches_an_active_transaction(self) -> None:
        catalogue, connection, minter = _catalogue()

        with self.assertRaisesRegex(duckdb.InvalidInputException, "Authorization"):
            with catalogue.remote_transaction():
                connection.failure_once = "Authorization failed"
                catalogue.remote_execute("INSERT INTO main.events VALUES (1)")

        self.assertEqual(minter.invalidated_generations, [])
        self.assertEqual(minter.minted, [])
        self.assertFalse(connection.closed)

    def test_last_committed_snapshot_is_session_local(self) -> None:
        catalogue, connection, _minter = _catalogue()
        connection.execute = lambda sql, parameters=None: (
            connection.calls.append((sql, parameters)) or _Cursor([(42,)])
        )

        snapshot = catalogue.last_committed_snapshot()

        self.assertEqual(snapshot, 42)
        self.assertEqual(
            connection.calls[-1],
            (
                "FROM quack_query_by_name(current_catalog(), ?)",
                ['SELECT id FROM "atlas".last_committed_snapshot()'],
            ),
        )


if __name__ == "__main__":
    unittest.main()
