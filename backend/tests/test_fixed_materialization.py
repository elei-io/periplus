import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pyarrow as pa

from materialization.executor import (
    MaterializationDependencyNotReady,
    _apply_html_element_delta,
    _arrow_table,
    _changed_html_hashes,
    _jsonld_rows,
    _jsonld_type_terms,
    _link_rows_for_documents,
    _materialize_jsonld_hashes,
    _merge_page_observation_rows,
    _merge_page_rows,
    _refresh_page_observations_incremental,
    _refresh_pages_incremental,
    _refresh_documents,
    _refresh_visits,
    _relation_scope,
    _replace_link_document_slices,
    _select_html_stage,
    workloads,
)
from materialization.lanes import MaterializationLanePool
from materialization.pipeline import StageExecution


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


    def test_jsonld_type_terms_are_distinct_raw_strings(self) -> None:
        value = {
            "@type": ["Article", "Thing", 42],
            "@graph": [{"@type": "Article"}, {"nested": {"@type": "Person"}}],
        }
        self.assertEqual(
            _jsonld_type_terms(value), {"Article", "Thing", "Person"}
        )

    def test_jsonld_refresh_ignores_script_with_valueless_type(self) -> None:
        self.assertEqual(
            _jsonld_rows([("content-hash", 0, {"type": None}, "")]),
            [],
        )

    def test_arrow_table_preserves_sparse_attribute_maps(self) -> None:
        table = _arrow_table(
            [
                {"element_index": 0, "attributes": {"lang": "en"}},
                {"element_index": 1, "attributes": {"href": "/next"}},
            ],
            map_columns={"attributes"},
        )

        self.assertEqual(
            table.schema.field("attributes").type,
            pa.map_(pa.string(), pa.string()),
        )
        self.assertEqual(
            [dict(value) for value in table["attributes"].to_pylist()],
            [{"lang": "en"}, {"href": "/next"}],
        )

    def test_html_changes_are_read_from_the_tick_snapshot_window(self) -> None:
        catalogue = MagicMock()
        catalogue.config.alias = "atlas"
        catalogue.trusted_remote_rows.return_value = [
            ("hash-b",),
            ("hash-a",),
            ("hash-b",),
        ]

        hashes = _changed_html_hashes(
            catalogue,
            start_snapshot=41,
            end_snapshot=47,
        )

        self.assertEqual(hashes, {"hash-a", "hash-b"})
        sql = catalogue.trusted_remote_rows.call_args.args[0]
        self.assertIn(
            "ducklake_table_changes(\n          'atlas', "
            "'ingest', 'documents',\n          41, 47",
            sql,
        )
        self.assertIn("lower(detected_media_type) = 'text/html'", sql)

    def test_html_delta_uses_scoped_delete_merge_and_bounded_insert(self) -> None:
        catalogue = MagicMock()

        _apply_html_element_delta(
            catalogue,
            rows=[
                {
                    "content_sha256": "new",
                    "element_index": 0,
                    "parent_index": None,
                    "subtree_end_index": 1,
                    "depth": 0,
                    "child_index": 0,
                    "tag": "html",
                    "namespace": "HTML",
                    "attributes": {},
                    "text_direct": "",
                    "text_tail": "",
                }
            ],
            removed_hashes={"orphaned"},
        )

        inserts = [
            call.args[0]
            for call in catalogue.trusted_connection.execute.call_args_list
        ]
        mutations = [
            call.args[0]
            for call in catalogue.trusted_remote_execute.call_args_list
        ]
        self.assertEqual(len(inserts), 1)
        self.assertIn("INSERT INTO material.html_elements", inserts[0])
        self.assertEqual(len(mutations), 1)
        self.assertIn("MERGE INTO material.html_elements", mutations[0])
        self.assertIn("WHEN MATCHED THEN DELETE", mutations[0])
        self.assertNotIn("DELETE FROM material.html_elements", mutations[0])

    @patch(
        "materialization.executor._covered_html_hashes",
        return_value={"covered", "removed"},
    )
    @patch(
        "materialization.executor._html_documents",
        return_value=[
            ("covered", "covered-key", "identity"),
            ("new", "new-key", "zstd"),
        ],
    )
    @patch(
        "materialization.executor._changed_html_hashes",
        return_value={"covered", "new", "removed"},
    )
    def test_html_stage_selects_only_uncovered_content(
        self,
        _changed_html_hashes,
        _html_documents,
        _covered_html_hashes,
    ) -> None:
        catalogue = MagicMock()
        catalogue.trusted_remote_rows.return_value = [
            ("covered", 10),
            ("new", 20),
        ]

        selection = _select_html_stage(catalogue, 41, 47)

        self.assertEqual(len(selection.items), 1)
        self.assertEqual(selection.items[0].content_sha256, "new")
        self.assertEqual(selection.items[0].content_bytes, 20)
        self.assertEqual(
            selection.initial_outputs[0].removed_hashes,
            {"removed"},
        )

    @patch("materialization.executor._replace_content_hash_slices")
    def test_jsonld_replaces_only_changed_hash_slices(
        self,
        replace_content_hash_slices,
    ) -> None:
        catalogue = MagicMock()
        catalogue.trusted_remote_rows.return_value = [
            (
                "hash-a",
                4,
                {"type": "application/ld+json"},
                '{"@type":"Article"}',
            )
        ]

        result = _materialize_jsonld_hashes(
            catalogue,
            {"hash-a"},
        )

        self.assertEqual(result, 1)
        replace_content_hash_slices.assert_called_once_with(
            catalogue,
            table_name="jsonld_values",
            content_hashes={"hash-a"},
            rows=[
                {
                    "content_sha256": "hash-a",
                    "element_index": 4,
                    "type_terms": ["Article"],
                    "value": '{"@type":"Article"}',
                }
            ],
            variant_columns={"value"},
        )

    @patch("materialization.executor._merge_page_rows")
    def test_pages_process_only_observed_visit_changes(self, merge_page_rows) -> None:
        catalogue = MagicMock()
        catalogue.config.alias = "atlas"
        catalogue.trusted_remote_rows.return_value = [
            ("HTTPS://Example.COM/path",),
        ]

        _refresh_pages_incremental(
            catalogue,
            MagicMock(),
            (SimpleNamespace(start_snapshot=20, end_snapshot=24),),
        )

        sql = catalogue.trusted_remote_rows.call_args.args[0]
        self.assertIn("'ingest', 'visits'", sql)
        self.assertIn("observed_at IS NOT NULL", sql)
        merge_page_rows.assert_called_once()
        row = merge_page_rows.call_args.args[1][0]
        self.assertEqual(row["normalized_url"], "https://example.com/path")

    def test_page_delta_uses_merge_insert(self) -> None:
        catalogue = MagicMock()

        _merge_page_rows(
            catalogue,
            [
                {
                    "page_id": "ff1af9a0-4cb5-5db0-af16-99859986d52f",
                    "normalized_url": "https://example.com/",
                    "scheme": "https",
                    "hostname": "example.com",
                    "port": None,
                    "path": "/",
                    "query": None,
                    "registrable_domain": "example.com",
                }
            ],
        )

        sql = catalogue.trusted_remote_execute.call_args.args[0]
        self.assertIn("MERGE INTO material.pages", sql)
        self.assertIn("target.page_id = delta.page_id", sql)
        self.assertIn("WHEN NOT MATCHED THEN INSERT", sql)
        self.assertNotIn("DELETE FROM material.pages", sql)

    @patch("materialization.executor._merge_page_observation_rows")
    def test_page_observations_cover_changed_visit_evidence(
        self,
        merge_page_observation_rows,
    ) -> None:
        observed_at = datetime.fromisoformat("2026-01-02T03:04:05+00:00")
        catalogue = MagicMock()
        catalogue.config.alias = "atlas"
        catalogue.trusted_remote_rows.return_value = [
            (
                "7c1f63ab-42c9-47d8-8221-54127e15c5e7",
                "cd7ea411-330c-5492-93ac-f804eb2a3859",
                "HTTPS://Example.COM/path",
                observed_at,
            ),
        ]

        _refresh_page_observations_incremental(
            catalogue,
            MagicMock(),
            (SimpleNamespace(start_snapshot=20, end_snapshot=24),),
        )

        sql = catalogue.trusted_remote_rows.call_args.args[0]
        self.assertIn("'ingest', 'visits'", sql)
        self.assertIn("observed_at IS NOT NULL", sql)
        merge_page_observation_rows.assert_called_once_with(
            catalogue,
            [
                {
                    "page_id": "b36ac90b-1dd2-5eb2-997a-ba9897d39f30",
                    "visit_id": "7c1f63ab-42c9-47d8-8221-54127e15c5e7",
                    "document_id": "cd7ea411-330c-5492-93ac-f804eb2a3859",
                    "observed_at": observed_at,
                }
            ],
        )

    def test_page_observation_delta_is_keyed_by_page_and_visit(self) -> None:
        catalogue = MagicMock()
        observed_at = datetime.fromisoformat("2026-01-02T03:04:05+00:00")

        _merge_page_observation_rows(
            catalogue,
            [
                {
                    "page_id": "b36ac90b-1dd2-5eb2-997a-ba9897d39f30",
                    "visit_id": "7c1f63ab-42c9-47d8-8221-54127e15c5e7",
                    "document_id": "cd7ea411-330c-5492-93ac-f804eb2a3859",
                    "observed_at": observed_at,
                }
            ],
        )

        sql = catalogue.trusted_remote_execute.call_args.args[0]
        self.assertIn("MERGE INTO material.page_observations", sql)
        self.assertIn("target.page_id = delta.page_id", sql)
        self.assertIn("target.visit_id = delta.visit_id", sql)
        self.assertIn("WHEN NOT MATCHED THEN INSERT", sql)

    @patch("materialization.executor._elements_by_hash")
    def test_links_project_pair_and_document_owned_observation(
        self,
        elements_by_hash,
    ) -> None:
        observed_at = datetime.fromisoformat("2026-01-02T03:04:05+00:00")
        elements_by_hash.return_value = {
            "hash-a": [
                SimpleNamespace(
                    element_index=0,
                    parent_index=None,
                    tag="html",
                    attributes={},
                    text_direct="",
                    text_tail="",
                ),
                SimpleNamespace(
                    element_index=1,
                    parent_index=0,
                    tag="a",
                    attributes={"href": "/next"},
                    text_direct="Next",
                    text_tail="",
                ),
            ]
        }

        output = _link_rows_for_documents(
            MagicMock(),
            [
                (
                    "cd7ea411-330c-5492-93ac-f804eb2a3859",
                    "hash-a",
                    "https://example.com/base",
                    observed_at,
                )
            ],
        )

        self.assertEqual(len(output.link_rows), 1)
        self.assertEqual(len(output.observation_rows), 1)
        self.assertEqual(
            output.observation_rows[0]["link_id"],
            output.link_rows[0]["link_id"],
        )
        self.assertEqual(
            output.observation_rows[0]["document_id"],
            "cd7ea411-330c-5492-93ac-f804eb2a3859",
        )
        self.assertEqual(output.observation_rows[0]["element_index"], 1)
        self.assertEqual(output.observation_rows[0]["raw_href"], "/next")
        self.assertNotIn("first_seen_at", output.link_rows[0])

    @patch("materialization.executor._elements_by_hash", return_value={})
    def test_links_retry_when_html_projection_is_not_ready(
        self,
        _elements_by_hash,
    ) -> None:
        with self.assertRaises(MaterializationDependencyNotReady):
            _link_rows_for_documents(
                MagicMock(),
                [
                    (
                        "cd7ea411-330c-5492-93ac-f804eb2a3859",
                        "missing",
                        "https://example.com/",
                        datetime.fromisoformat(
                            "2026-01-02T03:04:05+00:00"
                        ),
                    )
                ],
            )

    def test_link_delta_streams_missing_pairs_and_replaces_document_slice(
        self,
    ) -> None:
        catalogue = MagicMock()
        catalogue.trusted_remote_rows.return_value = []
        observed_at = datetime.fromisoformat("2026-01-02T03:04:05+00:00")

        output = _link_rows_for_documents(
            MagicMock(),
            [],
        )
        output = type(output)(
            document_ids=frozenset(
                {"cd7ea411-330c-5492-93ac-f804eb2a3859"}
            ),
            link_rows=[
                {
                    "link_id": "1091ed91-eec0-5bd9-b90c-8699c4d748dd",
                    "source_page_id": "463802e4-2f8a-58f2-bac0-3f6080ec8588",
                    "target_page_id": "6b1ac597-4b02-512d-9905-2c457bb69aa9",
                    "source_url": "https://example.com/",
                    "target_url": "https://example.com/next",
                    "relation_scope": "same_origin",
                }
            ],
            observation_rows=[
                {
                    "link_id": "1091ed91-eec0-5bd9-b90c-8699c4d748dd",
                    "document_id": "cd7ea411-330c-5492-93ac-f804eb2a3859",
                    "content_sha256": "hash-a",
                    "element_index": 1,
                    "raw_href": "/next",
                    "observed_at": observed_at,
                }
            ],
        )
        _replace_link_document_slices(
            catalogue,
            output,
        )

        self.assertEqual(catalogue.append.call_count, 2)
        self.assertEqual(
            catalogue.append.call_args_list[0].args[0],
            "links",
        )
        self.assertEqual(
            catalogue.append.call_args_list[1].args[0],
            "link_observations",
        )
        sql = catalogue.trusted_remote_execute.call_args.args[0]
        self.assertIn("DELETE FROM material.link_observations", sql)
        self.assertIn("document_id IN", sql)
        self.assertNotIn("MERGE", sql)

    def test_relation_scope_precedence(self) -> None:
        source = "https://www.example.com/a"
        self.assertEqual(_relation_scope(source, source), "self")
        self.assertEqual(
            _relation_scope(source, "https://www.example.com/b"),
            "same_origin",
        )
        self.assertEqual(
            _relation_scope(source, "http://www.example.com/b"), "same_host"
        )
        self.assertEqual(
            _relation_scope(source, "https://api.example.com/b"), "same_site"
        )
        self.assertEqual(
            _relation_scope(source, "https://example.net/b"), "external"
        )


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
        self.assertEqual(
            [reporter.active_operation_count for reporter in reporters],
            [1] * 8,
        )
        release.set()
        self.assertEqual(await asyncio.gather(*tasks), list(range(8)))
        self.assertEqual(
            [reporter.active_operation_count for reporter in reporters],
            [0] * 8,
        )
        self.assertEqual(await pool.call(lambda: "reused"), "reused")

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
            "materialization.executor._run_stage",
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

    async def test_document_stage_results_are_reported_after_dependencies(
        self,
    ) -> None:
        def result(*, items, partitions, rows):
            return StageExecution(
                source_items=items,
                source_bytes=10,
                partitions=partitions,
                output_rows=rows,
                select_seconds=0.1,
                project_seconds=0.2,
                write_seconds=0.3,
                elapsed_seconds=0.4,
            )

        async def execute(_leases, _pool, plan, *_args):
            if plan.target == "html_elements":
                return result(items=2, partitions=1, rows=20)
            return result(items=3, partitions=2, rows=30)

        async def run_stage(*_args):
            return 4

        pool = AsyncMock()
        pool.capacity = 8
        pool.call.return_value = {"hash-a", "hash-b"}
        with (
            patch(
                "materialization.executor.execute_bounded_stage",
                side_effect=execute,
            ),
            patch(
                "materialization.executor._run_stage",
                side_effect=run_stage,
            ),
        ):
            await _refresh_documents(
                MagicMock(),
                pool,
                MagicMock(),
                (SimpleNamespace(start_snapshot=1, end_snapshot=2),),
            )


if __name__ == "__main__":
    unittest.main()
