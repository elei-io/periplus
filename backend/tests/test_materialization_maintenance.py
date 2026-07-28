import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

from materialization.contracts import (
    ordered_projections,
    workload_projections,
)
from materialization.maintenance import (
    _within_byte_budget,
    generation_table,
    materialize_visit_batch,
)
from materialization.runtime import _source_highwater


class MaterializationMaintenanceTests(unittest.TestCase):
    def test_requested_tables_remain_independently_selectable(self) -> None:
        self.assertEqual(
            ordered_projections({"html_elements"}),
            ("html_elements",),
        )
        self.assertEqual(
            ordered_projections({"links", "page_observations"}),
            ("links", "page_observations"),
        )

    def test_selected_document_tables_share_one_workload(self) -> None:
        stages = (
            "html_elements",
            "jsonld_values",
            "link_observations",
            "pages",
        )
        self.assertEqual(
            workload_projections(stages, "html_elements"),
            ("html_elements", "jsonld_values", "link_observations"),
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

    def test_byte_budget_always_allows_one_source_item(self) -> None:
        rows = [
            ("first", "key", "identity", 20),
            ("second", "key", "identity", 2),
        ]
        self.assertEqual(
            _within_byte_budget(rows, 10, size_index=3),
            rows[:1],
        )

    def test_visit_catchup_replaces_deleted_and_corrected_visits(self) -> None:
        catalogue = MagicMock()
        catalogue.config.alias = "atlas"
        catalogue.trusted_remote_rows.side_effect = [
            [("visit-a",), ("visit-deleted",)],
            [
                (
                    "visit-a",
                    None,
                    "https://example.com/two",
                    datetime.fromisoformat(
                        "2026-01-02T03:04:05+00:00"
                    ),
                )
            ],
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

        self.assertEqual(result.cursor, "visit-deleted")
        sql = "\n".join(
            call.args[0]
            for call in catalogue.trusted_remote_execute.call_args_list
        )
        self.assertIn(
            "DELETE FROM material._atlas_rebuild_page_observations_run",
            sql,
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
