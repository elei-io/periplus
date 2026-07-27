import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from materialization.maintenance import (
    _within_byte_budget,
    dependency_closure,
    generation_table,
    materialize_catchup_batch,
)
from materialization.runtime import _source_highwater


class MaterializationMaintenanceTests(unittest.TestCase):
    def test_html_rebuild_closes_over_semantic_dependants(self) -> None:
        self.assertEqual(
            dependency_closure({"html_elements"}),
            (
                "html_elements",
                "jsonld_values",
                "links",
                "link_observations",
            ),
        )
        self.assertEqual(
            dependency_closure({"links"}),
            ("links", "link_observations"),
        )
        self.assertEqual(
            dependency_closure({"pages"}),
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

    @patch("materialization.maintenance._merge_page_rows")
    def test_page_catchup_is_cursor_bounded_and_replay_safe(
        self,
        merge_page_rows,
    ) -> None:
        catalogue = MagicMock()
        catalogue.config.alias = "atlas"
        catalogue.trusted_remote_rows.return_value = [
            (
                "00000000-0000-0000-0000-000000000002",
                None,
                "https://example.com/two",
                object(),
            ),
            (
                "00000000-0000-0000-0000-000000000003",
                None,
                "https://example.com/three",
                object(),
            ),
        ]

        result = materialize_catchup_batch(
            catalogue,
            MagicMock(),
            stage="pages",
            after_snapshot=10,
            through_snapshot=20,
            after_cursor="00000000-0000-0000-0000-000000000001",
            item_budget=2,
            byte_budget=1024,
            destinations={"pages": "_atlas_rebuild_pages_run"},
        )

        self.assertEqual(result.source_items, 2)
        self.assertEqual(
            result.cursor,
            "00000000-0000-0000-0000-000000000003",
        )
        sql = catalogue.trusted_remote_rows.call_args.args[0]
        self.assertIn("11, 20", sql)
        self.assertIn("LIMIT 2", sql)
        self.assertIn(
            "visits.visit_id::VARCHAR > "
            "'00000000-0000-0000-0000-000000000001'",
            sql,
        )
        merge_page_rows.assert_called_once()
        self.assertEqual(
            merge_page_rows.call_args.kwargs["table_name"],
            "_atlas_rebuild_pages_run",
        )

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
