from __future__ import annotations

import unittest
from unittest.mock import Mock

from repository.catalogue.compiler_definitions import (
    CatalogueCompilerDefinitionCache,
    CatalogueCompilerSnapshotChanged,
    read_catalogue_compiler_definitions,
)


class CatalogueCompilerDefinitionSnapshotTests(unittest.TestCase):
    def test_reads_macro_and_view_sql_from_ducklake_metadata(self) -> None:
        connection = Mock()
        connection.execute.return_value.fetchall.side_effect = [
            [(42,)],
            [
                (
                    "macros",
                    "normalize",
                    "macro",
                    ["value"],
                    "lower(trim(value))",
                ),
                (
                    "macros",
                    "selected",
                    "table_macro",
                    ["minimum"],
                    "SELECT * FROM source WHERE key >= minimum",
                ),
            ],
            [
                (
                    "views",
                    "live_records",
                    "CREATE VIEW views.live_records AS SELECT * FROM source",
                )
            ],
            [("main", "safe_udf", False, "CONSISTENT")],
            [
                (
                    "main",
                    "documents",
                    "118a68e5-3683-4f2d-aa28-204bb36c4f30",
                    1000,
                    8,
                    32_000,
                )
            ],
            [(42,)],
        ]

        snapshot = read_catalogue_compiler_definitions(
            connection,
            catalogue_alias="atlas",
        )

        self.assertEqual(snapshot.scalar_macros[0].macro_name, "normalize")
        self.assertEqual(snapshot.table_macros[0].macro_name, "selected")
        self.assertEqual(snapshot.table_macros[0].parameter_defaults, ())
        self.assertEqual(snapshot.views[0].view_name, "live_records")
        self.assertEqual(snapshot.views[0].sql, "SELECT * FROM source")
        self.assertEqual(
            snapshot.scalar_functions[0].function_name,
            "safe_udf",
        )
        self.assertTrue(snapshot.revision)
        self.assertEqual(snapshot.tables[0].table_name, "documents")
        self.assertEqual(snapshot.tables[0].contract_version, "1.0.0")
        self.assertEqual(snapshot.tables[0].estimated_rows, 1000)
        self.assertEqual(snapshot.tables[0].file_size_bytes, 32_000)

    def test_retries_when_catalogue_changes_during_snapshot_read(self) -> None:
        connection = Mock()
        empty_snapshot = [[], [], [], []]
        connection.execute.return_value.fetchall.side_effect = [
            [(1,)],
            *empty_snapshot,
            [(2,)],
            [(2,)],
            *empty_snapshot,
            [(2,)],
        ]

        snapshot = read_catalogue_compiler_definitions(
            connection,
            catalogue_alias="atlas",
        )

        self.assertTrue(snapshot.revision)
        self.assertEqual(connection.execute.call_count, 12)

    def test_fails_when_catalogue_never_stabilizes(self) -> None:
        connection = Mock()
        rows: list[list[tuple[int]] | list[object]] = []
        for snapshot in (1, 2, 3):
            rows.extend(
                [[(snapshot,)], [], [], [], [], [(snapshot + 1,)]]
            )
        connection.execute.return_value.fetchall.side_effect = rows

        with self.assertRaisesRegex(
            CatalogueCompilerSnapshotChanged,
            "three consecutive",
        ):
            read_catalogue_compiler_definitions(
                connection,
                catalogue_alias="atlas",
            )


class CatalogueCompilerDefinitionCacheTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_caches_until_invalidated(self) -> None:
        load = Mock(
            side_effect=[
                _snapshot("one"),
                _snapshot("two"),
            ]
        )
        cache = CatalogueCompilerDefinitionCache(load, ttl_seconds=60)

        first = await cache.get()
        second = await cache.get()
        cache.invalidate()
        third = await cache.get()

        self.assertIs(first, second)
        self.assertEqual(first.revision, "one")
        self.assertEqual(third.revision, "two")
        self.assertEqual(load.call_count, 2)


def _snapshot(revision: str):
    from repository.catalogue.compiler_definitions import (
        CatalogueCompilerDefinitions,
    )

    return CatalogueCompilerDefinitions(
        revision=revision,
        scalar_macros=(),
        table_macros=(),
        views=(),
        scalar_functions=(),
    )


if __name__ == "__main__":
    unittest.main()
