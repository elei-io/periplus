from __future__ import annotations

from types import SimpleNamespace
import unittest

import duckdb

from repository.catalogue.client import Catalogue
from repository.catalogue.config import CatalogueConfig


class _Cursor:
    description = [("value",)]

    def __init__(self, rows: list[tuple] | None = None) -> None:
        self._rows = rows or []

    def fetchall(self) -> list[tuple]:
        return self._rows


class _Connection:
    def __init__(self, *, invalid_once: bool = False) -> None:
        self.calls: list[tuple[str, object | None]] = []
        self.closed = False
        self.invalid_once = invalid_once

    def execute(self, sql: str, parameters=None) -> _Cursor:
        self.calls.append((sql, parameters))
        if self.invalid_once:
            self.invalid_once = False
            raise duckdb.InvalidInputException("Invalid connection id")
        return _Cursor()

    def close(self) -> None:
        self.closed = True


class _Minter:
    def __init__(self) -> None:
        self.closed = False
        self.minted: list[SimpleNamespace] = []

    def mint(self, *, duckdb_config=None) -> SimpleNamespace:
        connection = _Connection()
        minted = SimpleNamespace(
            connection=connection,
            session_id=f"{len(self.minted) + 1:016x}",
            catalogue_alias="atlas",
            close=connection.close,
        )
        self.minted.append(minted)
        return minted

    def close(self) -> None:
        self.closed = True


def _catalogue() -> tuple[Catalogue, _Connection, _Minter]:
    connection = _Connection()
    minter = _Minter()
    minted = SimpleNamespace(
        connection=connection,
        session_id="0123456789abcdef",
        catalogue_alias="atlas",
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
            catalogue_alias="atlas",
            close=connection.close,
        )
        catalogue = Catalogue(
            CatalogueConfig(alias="atlas"),
            minted=minted,
            minter=minter,
            duckdb_config={"memory_limit": "1GB"},
        )
        connection.invalid_once = True

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
            catalogue_alias="atlas",
            close=connection.close,
        )
        catalogue = Catalogue(
            CatalogueConfig(alias="atlas"),
            minted=minted,
            minter=minter,
        )
        connection.invalid_once = True

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


if __name__ == "__main__":
    unittest.main()
