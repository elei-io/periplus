import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

from atlas.materialization.contracts import (
    ordered_projections,
    workload_projections,
)
from atlas.materialization.maintenance import (
    _select_document_scan,
    _select_link_rollup_batch,
    _within_byte_budget,
    generation_table,
    materialize_visit_batch,
    prepare_rebuild,
)
from atlas.materialization.runtime import _source_highwater


class MaterializationMaintenanceTests(unittest.TestCase):
    def test_dependent_tables_are_rebuilt_together(self) -> None:
        self.assertEqual(
            ordered_projections({"html_elements"}),
            ("html_elements",),
        )
        self.assertEqual(
            ordered_projections({"links", "page_observations"}),
            (
                "links",
                "link_occurrences",
                "page_observations",
                "page_heads",
            ),
        )

    def test_selected_document_tables_share_one_workload(self) -> None:
        stages = (
            "html_elements",
            "jsonld_values",
            "link_occurrences",
            "pages",
        )
        self.assertEqual(
            workload_projections(stages, "html_elements"),
            ("html_elements", "jsonld_values", "link_occurrences"),
        )
        self.assertEqual(
            workload_projections(stages, "pages"),
            ("pages",),
        )

    def test_generation_names_are_deterministic_and_internal(self) -> None:
        run_id = UUID("705ca93c-11fe-48d8-833e-c454ee726668")
        self.assertEqual(
            generation_table("jsonld_values", run_id),
            "_atlas_rebuild_jsonld_values_705ca93c11fe48d8",
        )

    def test_rebuild_generations_are_created_atomically(self) -> None:
        catalogue = MagicMock()
        run_id = UUID("705ca93c-11fe-48d8-833e-c454ee726668")

        destinations = prepare_rebuild(
            catalogue,
            run_id,
            ("content_stats", "pages"),
        )

        self.assertEqual(
            destinations,
            {
                "content_stats": (
                    "_atlas_rebuild_content_stats_705ca93c11fe48d8"
                ),
                "pages": "_atlas_rebuild_pages_705ca93c11fe48d8",
            },
        )
        catalogue.create_materialization_generations.assert_called_once()
        self.assertEqual(
            catalogue.create_materialization_generations.call_args.kwargs[
                "generation_id"
            ],
            run_id.hex,
        )

    def test_byte_budget_always_allows_one_source_item(self) -> None:
        rows = [
            ("first", "key", "identity", 20),
            ("second", "key", "identity", 2),
        ]
        self.assertEqual(
            _within_byte_budget(rows, 10, size_index=3),
            rows[:1],
        )

    def test_document_scan_finalizes_rollups_in_bounded_source_slices(
        self,
    ) -> None:
        catalogue = MagicMock()
        source_page_ids = [
            (f"00000000-0000-0000-0000-{index:012d}", 1)
            for index in range(1001)
        ]
        catalogue.trusted_remote_rows.side_effect = [
            [],
            source_page_ids,
        ]

        selection = _select_document_scan(
            catalogue,
            snapshot=10,
            after_cursor="last-document",
            item_budget=100,
            byte_budget=1024,
            links_table="_atlas_rebuild_links_run",
        )

        self.assertFalse(selection.done)
        self.assertEqual(selection.items, ())
        self.assertEqual(len(selection.initial_outputs), 1)
        self.assertEqual(
            len(
                selection.initial_outputs[
                    0
                ].finalize_link_source_page_ids
            ),
            1000,
        )
        self.assertEqual(
            selection.cursor,
            "link-rollups:00000000-0000-0000-0000-000000000999",
        )

        catalogue.trusted_remote_rows.side_effect = [
            [source_page_ids[-1]]
        ]
        final = _select_document_scan(
            catalogue,
            snapshot=10,
            after_cursor=selection.cursor,
            item_budget=100,
            byte_budget=1024,
            links_table="_atlas_rebuild_links_run",
        )
        self.assertTrue(final.done)
        self.assertEqual(
            final.initial_outputs[0].finalize_link_source_page_ids,
            frozenset({"00000000-0000-0000-0000-000000001000"}),
        )
        self.assertIn(
            "source_page_id::VARCHAR > "
            "'00000000-0000-0000-0000-000000000999'",
            catalogue.trusted_remote_rows.call_args.args[0],
        )

    def test_document_rollup_slice_is_also_bounded_by_link_rows(self) -> None:
        catalogue = MagicMock()
        catalogue.trusted_remote_rows.return_value = [
            ("00000000-0000-0000-0000-000000000001", 75_000),
            ("00000000-0000-0000-0000-000000000002", 30_000),
        ]

        selection = _select_link_rollup_batch(
            catalogue,
            links_table="_atlas_rebuild_links_run",
            after_cursor=None,
        )

        self.assertFalse(selection.done)
        self.assertEqual(
            selection.cursor,
            "link-rollups:00000000-0000-0000-0000-000000000001",
        )
        self.assertEqual(
            selection.initial_outputs[0].finalize_link_source_page_ids,
            frozenset({"00000000-0000-0000-0000-000000000001"}),
        )

    def test_visit_catchup_replaces_deleted_and_corrected_visits(self) -> None:
        catalogue = MagicMock()
        catalogue.config.alias = "atlas"
        visit_a = "10000000-0000-0000-0000-000000000001"
        visit_deleted = "10000000-0000-0000-0000-000000000002"
        catalogue.trusted_remote_rows.side_effect = [
            [(visit_a,), (visit_deleted,)],
            [
                (
                    visit_a,
                    None,
                    "https://example.com/two",
                    datetime.fromisoformat(
                        "2026-01-02T03:04:05+00:00"
                    ),
                )
            ],
            [],
        ]

        result = materialize_visit_batch(
            catalogue,
            stages=("page_observations",),
            source_snapshot=10,
            after_snapshot=10,
            through_snapshot=20,
            after_cursor=None,
            item_budget=2,
            destinations={
                "page_observations": "_atlas_rebuild_page_observations_run"
            },
        )

        self.assertEqual(result.cursor, visit_deleted)
        catalogue.commit_bulk_files.assert_called_once()
        mutations = catalogue.commit_bulk_files.call_args.args[0]
        self.assertEqual(
            [item.mutation_mode for item in mutations],
            ["delete", "replace"],
        )
        self.assertIn("AT (VERSION => 20)", (
            catalogue.trusted_remote_rows.call_args_list[1].args[0]
        ))

    def test_source_highwater_ignores_shadow_table_commits(self) -> None:
        catalogue = MagicMock()
        catalogue.config.alias = "atlas"
        catalogue.latest_snapshot.return_value = 100
        catalogue.trusted_remote_rows.return_value = [(73,)]
        run = SimpleNamespace(
            stages=("pages",),
            catchup_snapshot=60,
        )

        self.assertEqual(_source_highwater(catalogue, run), 73)
        sql = catalogue.trusted_remote_rows.call_args.args[0]
        self.assertIn("'ingest', 'visits'", sql)
        self.assertIn("61, 100", sql)


if __name__ == "__main__":
    unittest.main()
