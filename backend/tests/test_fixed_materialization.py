import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from atlas.materialization.document_sources import document_observation_rows
from atlas.materialization.document_workload import select_document_changes
from atlas.materialization.executor import (
    _refresh_documents,
    _refresh_page_observations_incremental,
    _refresh_visits,
    workloads,
)
from atlas.materialization.lanes import MaterializationLanePool
from atlas.materialization.pipeline import StageExecution
from atlas.materialization.visit_workload import (
    changed_visit_rows,
    merge_page_observation_rows,
)


class FixedMaterializationTests(unittest.TestCase):
    def test_topology_has_two_source_owned_workloads(self) -> None:
        self.assertEqual(
            [
                (
                    item.name,
                    item.source_schema,
                    item.source_table,
                    item.stages,
                    item.durable,
                )
                for item in workloads()
            ],
            [
                (
                    "documents",
                    "ingest",
                    "documents",
                    (
                        "html_elements",
                        "jsonld_values",
                        "links",
                        "link_observations",
                    ),
                    "atlas-material-documents-v1",
                ),
                (
                    "visits",
                    "ingest",
                    "visits",
                    ("pages", "page_observations"),
                    "atlas-material-visits-v1",
                ),
            ],
        )

    @patch(
        "atlas.materialization.document_workload._changed_document_corrections",
        return_value=set(),
    )
    @patch(
        "atlas.materialization.document_workload._covered_html_hashes",
        return_value=set(),
    )
    @patch(
        "atlas.materialization.document_workload.document_observation_rows",
        return_value=[],
    )
    @patch(
        "atlas.materialization.document_workload.changed_document_ids",
        return_value=[],
    )
    @patch(
        "atlas.materialization.document_workload._changed_html_hashes",
        return_value={"hash-a"},
    )
    def test_document_selection_pins_all_live_source_reads(
        self,
        _changed_hashes,
        _changed_ids,
        _source_rows,
        _covered,
        _corrections,
    ) -> None:
        catalogue = MagicMock()
        catalogue.trusted_remote_rows.side_effect = [
            [("hash-a", "object", "identity")],
            [("hash-a", 42)],
        ]

        selection = select_document_changes(catalogue, 41, 47)

        self.assertEqual(len(selection.items), 1)
        sql = "\n".join(
            call.args[0]
            for call in catalogue.trusted_remote_rows.call_args_list
        )
        self.assertEqual(sql.count("AT (VERSION => 47)"), 2)

    def test_document_observations_pin_documents_and_visits(self) -> None:
        catalogue = MagicMock()
        observed_at = datetime.fromisoformat("2026-01-02T03:04:05+00:00")
        catalogue.trusted_remote_rows.side_effect = [
            [("document-a", "visit-a", "hash-a", 42)],
            [
                (
                    "visit-a",
                    "https://example.com/",
                    observed_at,
                )
            ],
        ]

        rows = document_observation_rows(
            catalogue,
            ["document-a"],
            snapshot=47,
        )

        self.assertEqual(
            rows,
            [
                (
                    "document-a",
                    "hash-a",
                    "https://example.com/",
                    observed_at,
                    42,
                )
            ],
        )
        sql = "\n".join(
            call.args[0]
            for call in catalogue.trusted_remote_rows.call_args_list
        )
        self.assertEqual(sql.count("AT (VERSION => 47)"), 2)

    def test_visit_changes_resolve_current_rows_at_tick_snapshot(self) -> None:
        catalogue = MagicMock()
        catalogue.config.alias = "atlas"
        catalogue.trusted_remote_rows.side_effect = [
            [("visit-a",), ("visit-b",)],
            [
                (
                    "visit-a",
                    None,
                    "https://example.com/",
                    datetime.fromisoformat("2026-01-02T03:04:05+00:00"),
                )
            ],
        ]

        visit_ids, rows = changed_visit_rows(
            catalogue,
            start_snapshot=20,
            end_snapshot=24,
        )

        self.assertEqual(visit_ids, {"visit-a", "visit-b"})
        self.assertEqual(len(rows), 1)
        changes_sql = catalogue.trusted_remote_rows.call_args_list[0].args[0]
        current_sql = catalogue.trusted_remote_rows.call_args_list[1].args[0]
        self.assertNotIn("change_type IN", changes_sql)
        self.assertIn("AT (VERSION => 24)", current_sql)

    @patch("atlas.materialization.executor.merge_page_observation_rows")
    def test_observation_refresh_replaces_changed_visit_slices(
        self,
        merge,
    ) -> None:
        observed_at = datetime.fromisoformat("2026-01-02T03:04:05+00:00")
        with patch(
            "atlas.materialization.executor.changed_visit_rows",
            return_value=(
                frozenset({"visit-a", "visit-deleted"}),
                [
                    (
                        "visit-a",
                        None,
                        "https://example.com/",
                        observed_at,
                    )
                ],
            ),
        ):
            _refresh_page_observations_incremental(
                MagicMock(),
                MagicMock(),
                (SimpleNamespace(start_snapshot=20, end_snapshot=24),),
            )

        self.assertEqual(
            merge.call_args.kwargs["replaced_visit_ids"],
            {"visit-a", "visit-deleted"},
        )

    def test_page_observation_merge_is_keyed_by_visit(self) -> None:
        catalogue = MagicMock()
        observed_at = datetime.fromisoformat("2026-01-02T03:04:05+00:00")

        merge_page_observation_rows(
            catalogue,
            [
                {
                    "page_id": "b36ac90b-1dd2-5eb2-997a-ba9897d39f30",
                    "visit_id": "7c1f63ab-42c9-47d8-8221-54127e15c5e7",
                    "document_id": None,
                    "observed_at": observed_at,
                }
            ],
            replaced_visit_ids=frozenset(
                {"7c1f63ab-42c9-47d8-8221-54127e15c5e7"}
            ),
        )

        sql = "\n".join(
            call.args[0]
            for call in catalogue.trusted_remote_execute.call_args_list
        )
        self.assertIn("DELETE FROM material.page_observations", sql)
        self.assertIn("ON target.visit_id = delta.visit_id", sql)
        self.assertIn("WHEN MATCHED THEN UPDATE", sql)


class MaterializationLanePoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_lanes_are_shared_and_reusable(self) -> None:
        release = asyncio.Event()
        entered = 0
        all_entered = asyncio.Event()

        class Lane:
            async def call(self, operation, *args):
                nonlocal entered
                entered += 1
                if entered == 8:
                    all_entered.set()
                await release.wait()
                return operation(*args)

        reporters = tuple(
            SimpleNamespace(active_operation_count=0) for _ in range(8)
        )
        pool = MaterializationLanePool(
            [Lane() for _ in range(8)],
            reporters,
        )
        tasks = [
            asyncio.create_task(pool.call(lambda value: value, index))
            for index in range(8)
        ]

        await asyncio.wait_for(all_entered.wait(), timeout=1)
        release.set()
        self.assertEqual(await asyncio.gather(*tasks), list(range(8)))
        self.assertEqual(
            [reporter.active_operation_count for reporter in reporters],
            [0] * 8,
        )

    async def test_pool_wide_metadata_barrier_visits_every_lane(self) -> None:
        calls: list[int] = []

        class Lane:
            def __init__(self, index: int) -> None:
                self.index = index

            async def call(self, operation, *args):
                calls.append(self.index)
                return operation(self.index, *args)

        reporters = tuple(
            SimpleNamespace(active_operation_count=0) for _ in range(8)
        )
        pool = MaterializationLanePool(
            [Lane(index) for index in range(8)],
            reporters,
        )

        results = await pool.call_all(lambda index: index)

        self.assertCountEqual(calls, range(8))
        self.assertCountEqual(results, range(8))
        self.assertEqual(
            [reporter.active_operation_count for reporter in reporters],
            [0] * 8,
        )

    async def test_visit_stages_run_concurrently(self) -> None:
        release = asyncio.Event()
        both_started = asyncio.Event()
        started: list[str] = []

        async def run_stage(_leases, _pool, target, _operation, *_args):
            started.append(target)
            if len(started) == 2:
                both_started.set()
            await release.wait()

        with patch(
            "atlas.materialization.executor._run_stage",
            side_effect=run_stage,
        ):
            operation = asyncio.create_task(
                _refresh_visits(
                    MagicMock(),
                    MagicMock(),
                    MagicMock(),
                    (SimpleNamespace(start_snapshot=1, end_snapshot=2),),
                )
            )
            await asyncio.wait_for(both_started.wait(), timeout=1)
            self.assertCountEqual(
                started,
                ["pages", "page_observations"],
            )
            release.set()
            await operation

    async def test_document_workload_uses_one_bounded_plan(self) -> None:
        result = StageExecution(
            source_items=2,
            source_bytes=10,
            partitions=1,
            write_partitions=1,
            output_rows=20,
            select_seconds=0.1,
            project_seconds=0.2,
            write_seconds=0.3,
            elapsed_seconds=0.4,
        )
        pool = MagicMock()
        pool.capacity = 8
        with patch(
            "atlas.materialization.executor.execute_bounded_stage",
            return_value=result,
        ) as execute:
            await _refresh_documents(
                MagicMock(),
                pool,
                MagicMock(),
                (SimpleNamespace(start_snapshot=1, end_snapshot=2),),
            )

        execute.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
