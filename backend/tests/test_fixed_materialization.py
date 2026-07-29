import asyncio
import unittest
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import duckdb
import pyarrow as pa

from atlas.materialization.document_projection import DocumentProjection
from atlas.materialization.document_sources import document_observation_rows
from atlas.materialization.document_workload import (
    DocumentProjectionOutput,
    apply_document_corrections,
    select_document_changes,
    write_document_output,
)
from atlas.materialization.executor import (
    _refresh_documents,
    _refresh_page_observations_and_heads_incremental,
    _refresh_visits,
    workloads,
)
from atlas.materialization.lanes import MaterializationLanePool
from atlas.materialization.pipeline import StageExecution
from atlas.materialization.visit_workload import (
    changed_visit_rows,
    merge_page_head_rows,
    merge_page_observation_rows,
    rebuild_page_heads,
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
                        "content_stats",
                        "html_elements",
                        "jsonld_values",
                        "links",
                        "link_occurrences",
                    ),
                    "atlas-material-documents-v1",
                ),
                (
                    "visits",
                    "ingest",
                    "visits",
                    ("pages", "page_observations", "page_heads"),
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
                    "visit-a",
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

    @patch("atlas.materialization.executor.rebuild_page_heads")
    @patch("atlas.materialization.executor.merge_page_observation_rows")
    def test_observation_refresh_replaces_changed_visit_slices(
        self,
        merge,
        rebuild_heads,
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
            _refresh_page_observations_and_heads_incremental(
                MagicMock(),
                MagicMock(),
                (SimpleNamespace(start_snapshot=20, end_snapshot=24),),
            )

        self.assertEqual(
            merge.call_args.kwargs["replaced_visit_ids"],
            {"visit-a", "visit-deleted"},
        )
        rebuild_heads.assert_called_once()

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
                    "visit_at": observed_at,
                }
            ],
            replaced_visit_ids=frozenset(
                {"7c1f63ab-42c9-47d8-8221-54127e15c5e7"}
            ),
        )

        catalogue.trusted_remote_execute.assert_not_called()
        catalogue.commit_bulk_files.assert_called_once()
        replacement = catalogue.commit_bulk_files.call_args.args[0][0]
        self.assertEqual(replacement.mutation_mode, "replace")
        self.assertEqual(replacement.match_columns, ("visit_id",))

    def test_page_head_is_latest_terminal_visit_with_stable_ties(
        self,
    ) -> None:
        catalogue = _LocalCatalogue()
        page_id = "b36ac90b-1dd2-5eb2-997a-ba9897d39f30"
        older = "10000000-0000-0000-0000-000000000001"
        tied_lower = "10000000-0000-0000-0000-000000000002"
        tied_higher = "10000000-0000-0000-0000-000000000003"
        early = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
        late = datetime.fromisoformat("2026-01-02T00:00:00+00:00")
        try:
            catalogue.connection.execute("CREATE SCHEMA material")
            catalogue.connection.execute(
                """
                CREATE TABLE material.page_observations (
                    page_id UUID,
                    visit_id UUID,
                    document_id UUID,
                    visit_at TIMESTAMPTZ
                );
                CREATE TABLE material.page_heads (
                    page_id UUID,
                    visit_id UUID,
                    visit_at TIMESTAMPTZ
                )
                """
            )
            rows = [
                {
                    "page_id": page_id,
                    "visit_id": older,
                    "document_id": None,
                    "visit_at": early,
                },
                {
                    "page_id": page_id,
                    "visit_id": tied_lower,
                    "document_id": None,
                    "visit_at": late,
                },
                {
                    "page_id": page_id,
                    "visit_id": tied_higher,
                    "document_id": None,
                    "visit_at": late,
                },
            ]
            merge_page_observation_rows(catalogue, rows)
            merge_page_head_rows(catalogue, rows)
            self.assertEqual(
                catalogue.connection.execute(
                    "SELECT visit_id::VARCHAR FROM material.page_heads"
                ).fetchone()[0],
                tied_higher,
            )

            catalogue.connection.execute(
                "DELETE FROM material.page_observations "
                f"WHERE visit_id = UUID '{tied_higher}'"
            )
            rebuild_page_heads(catalogue, {page_id})
            self.assertEqual(
                catalogue.connection.execute(
                    "SELECT visit_id::VARCHAR FROM material.page_heads"
                ).fetchone()[0],
                tied_lower,
            )
        finally:
            catalogue.connection.close()

    def test_shadow_generation_finalizes_one_source_page_slice(
        self,
    ) -> None:
        catalogue = _LocalCatalogue()
        try:
            catalogue.connection.execute(
                """
                CREATE SCHEMA material;
                CREATE TABLE material._atlas_rebuild_links_run (
                    link_id UUID,
                    source_page_id UUID,
                    target_page_id UUID,
                    source_url VARCHAR,
                    target_url VARCHAR,
                    relation_scope VARCHAR,
                    first_seen_at TIMESTAMPTZ,
                    last_seen_at TIMESTAMPTZ,
                    visit_count UBIGINT,
                    distinct_content_count UBIGINT,
                    occurrence_count UBIGINT
                );
                CREATE TABLE material._atlas_rebuild_link_occurrences_run (
                    link_id UUID,
                    visit_id UUID,
                    content_sha256 VARCHAR,
                    observed_at TIMESTAMPTZ
                );
                INSERT INTO material._atlas_rebuild_links_run VALUES
                    ('10000000-0000-0000-0000-000000000001',
                     '20000000-0000-0000-0000-000000000001',
                     '30000000-0000-0000-0000-000000000001',
                     'https://a.example/', 'https://one.example/',
                     'external', now(), now(), 0, 0, 0),
                    ('10000000-0000-0000-0000-000000000002',
                     '20000000-0000-0000-0000-000000000001',
                     '30000000-0000-0000-0000-000000000002',
                     'https://a.example/', 'https://two.example/',
                     'external', now(), now(), 0, 0, 0),
                    ('10000000-0000-0000-0000-000000000003',
                     '20000000-0000-0000-0000-000000000002',
                     '30000000-0000-0000-0000-000000000003',
                     'https://b.example/', 'https://three.example/',
                     'external', now(), now(), 0, 0, 0);
                INSERT INTO
                    material._atlas_rebuild_link_occurrences_run
                VALUES
                    ('10000000-0000-0000-0000-000000000001',
                     '40000000-0000-0000-0000-000000000001',
                     'content-a',
                     TIMESTAMPTZ '2026-01-01 00:00:00+00'),
                    ('10000000-0000-0000-0000-000000000001',
                     '40000000-0000-0000-0000-000000000002',
                     'content-a',
                     TIMESTAMPTZ '2026-01-02 00:00:00+00')
                """
            )

            written = write_document_output(
                catalogue,
                DocumentProjectionOutput(
                    finalize_link_source_page_ids=frozenset(
                        {"20000000-0000-0000-0000-000000000001"}
                    )
                ),
                targets={
                    "links": "_atlas_rebuild_links_run",
                    "link_occurrences":
                        "_atlas_rebuild_link_occurrences_run",
                },
                enabled_targets=frozenset(
                    {"links", "link_occurrences"}
                ),
            )

            self.assertEqual(written, 0)
            self.assertEqual(
                catalogue.connection.execute(
                    """
                    SELECT
                      link_id::VARCHAR,
                      visit_count,
                      distinct_content_count,
                      occurrence_count
                    FROM material._atlas_rebuild_links_run
                    ORDER BY link_id
                    """
                ).fetchall(),
                [
                    (
                        "10000000-0000-0000-0000-000000000001",
                        2,
                        1,
                        2,
                    ),
                    (
                        "10000000-0000-0000-0000-000000000003",
                        0,
                        0,
                        0,
                    ),
                ],
            )
        finally:
            catalogue.connection.close()

    @patch(
        "atlas.materialization.document_workload."
        "commit_document_projection",
        return_value=1,
    )
    @patch(
        "atlas.materialization.document_workload._missing_shadow_projection",
        side_effect=lambda _catalogue, projection, **_kwargs: projection,
    )
    def test_shadow_link_targets_use_one_bulk_commit(
        self,
        _missing,
        commit_projection,
    ) -> None:
        catalogue = MagicMock()
        projection = DocumentProjection(
            content_hashes=frozenset(),
            document_ids=frozenset(),
            content_stats=pa.table({}),
            html_elements=pa.table({}),
            jsonld_values=pa.table({}),
            links=pa.table(
                {
                    "link_id": ["link-a"],
                    "source_page_id": ["page-a"],
                }
            ),
            link_occurrences=pa.table(
                {"link_id": pa.array([], type=pa.string())}
            ),
        )

        written = write_document_output(
            catalogue,
            DocumentProjectionOutput(projection=projection),
            targets={
                "links": "_atlas_rebuild_links_run",
                "link_occurrences":
                    "_atlas_rebuild_link_occurrences_run",
            },
            enabled_targets=frozenset({"links", "link_occurrences"}),
        )

        self.assertEqual(written, 1)
        commit_projection.assert_called_once_with(
            catalogue,
            projection,
            targets={
                "content_stats": "content_stats",
                "html_elements": "html_elements",
                "jsonld_values": "jsonld_values",
                "links": "_atlas_rebuild_links_run",
                "link_occurrences":
                    "_atlas_rebuild_link_occurrences_run",
            },
            enabled_targets=frozenset({"links", "link_occurrences"}),
        )
        catalogue.remote_transaction.assert_not_called()
        catalogue.append_arrow.assert_not_called()

    def test_shadow_catchup_defers_link_rollups_to_finalizer(
        self,
    ) -> None:
        catalogue = MagicMock()

        apply_document_corrections(
            catalogue,
            DocumentProjectionOutput(
                replaced_document_ids=frozenset(
                    {"30000000-0000-0000-0000-000000000001"}
                )
            ),
            targets={
                "links": "_atlas_rebuild_links_run",
                "link_occurrences":
                    "_atlas_rebuild_link_occurrences_run",
            },
            enabled_targets=frozenset(
                {"links", "link_occurrences"}
            ),
        )

        catalogue.trusted_remote_rows.assert_not_called()
        catalogue.trusted_remote_execute.assert_not_called()
        catalogue.commit_bulk_files.assert_called_once()
        deletion = catalogue.commit_bulk_files.call_args.args[0][0]
        self.assertEqual(
            deletion.table,
            "_atlas_rebuild_link_occurrences_run",
        )
        self.assertEqual(deletion.mutation_mode, "delete")
        self.assertEqual(deletion.match_columns, ("document_id",))


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
            started.append(
                target[0] if isinstance(target, tuple) else target
            )
            if len(started) == 2:
                both_started.set()
            await release.wait()

        with patch(
            "atlas.materialization.executor._run_stage",
            side_effect=run_stage,
        ), patch(
            "atlas.materialization.executor._run_stage_group",
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


class _LocalCatalogue:
    def __init__(self) -> None:
        self.connection = duckdb.connect()

    @contextmanager
    def remote_transaction(self):
        self.connection.execute("BEGIN")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def trusted_remote_execute(self, sql: str) -> None:
        self.connection.execute(sql)

    def trusted_remote_rows(self, sql: str) -> list[tuple]:
        return self.connection.execute(sql).fetchall()

    def commit_bulk_files(self, files, *, idempotency_key: str):
        del idempotency_key
        self.connection.execute("BEGIN")
        try:
            for item in files:
                relation = (
                    'material."'
                    + item.table.replace('"', '""')
                    + '"'
                )
                if item.mutation_mode != "append":
                    predicates = " AND ".join(
                        'target."'
                        + column.replace('"', '""')
                        + '" IS NOT DISTINCT FROM source."'
                        + column.replace('"', '""')
                        + '"'
                        for column in item.match_columns
                    )
                    self.connection.execute(
                        f"DELETE FROM {relation} AS target "
                        "USING read_parquet(?) AS source "
                        f"WHERE {predicates}",
                        [str(item.path)],
                    )
                if item.mutation_mode != "delete":
                    self.connection.execute(
                        f"INSERT INTO {relation} BY NAME "
                        "SELECT * FROM read_parquet(?)",
                        [str(item.path)],
                    )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return SimpleNamespace(snapshot_id=1)


if __name__ == "__main__":
    unittest.main()
