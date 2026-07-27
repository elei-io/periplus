import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pyarrow as pa

from materialization.executor import (
    MaterializationDependencyNotReady,
    _apply_html_element_delta,
    _arrow_table,
    _changed_html_hashes,
    _jsonld_type_terms,
    _materialize_html_hashes,
    _merge_link_rows,
    _merge_page_observation_rows,
    _merge_page_rows,
    _refresh_html_elements_incremental,
    _refresh_jsonld_values,
    _refresh_jsonld_values_incremental,
    _refresh_links_incremental,
    _refresh_page_observations_incremental,
    _refresh_pages_incremental,
    _relation_scope,
    _requires_startup_backfill,
    workloads,
)


class FixedMaterializationTests(unittest.TestCase):
    def test_topology_is_fixed_and_has_one_source_per_target(self) -> None:
        self.assertEqual(
            [
                (
                    item.source_schema,
                    item.source_table,
                    item.target_table,
                    item.durable,
                )
                for item in workloads()
            ],
            [
                (
                    "ingest",
                    "documents",
                    "html_elements",
                    "atlas-material-html_elements-v1",
                ),
                (
                    "material",
                    "html_elements",
                    "jsonld_values",
                    "atlas-material-jsonld_values-v1",
                ),
                (
                    "ingest",
                    "visits",
                    "pages",
                    "atlas-material-pages-v1",
                ),
                (
                    "ingest",
                    "visits",
                    "page_observations",
                    "atlas-material-page_observations-v1",
                ),
                (
                    "ingest",
                    "documents",
                    "links",
                    "atlas-material-links-v1",
                ),
            ],
        )

    def test_only_new_durable_consumers_require_full_non_html_backfill(self) -> None:
        by_name = {workload.name: workload for workload in workloads()}
        new_consumer = SimpleNamespace(
            delivered=SimpleNamespace(consumer_seq=0)
        )
        existing_consumer = SimpleNamespace(
            delivered=SimpleNamespace(consumer_seq=42)
        )

        self.assertTrue(
            _requires_startup_backfill(
                by_name["jsonld_values"],
                new_consumer,
            )
        )
        self.assertFalse(
            _requires_startup_backfill(
                by_name["jsonld_values"],
                existing_consumer,
            )
        )
        self.assertTrue(
            _requires_startup_backfill(
                by_name["html_elements"],
                existing_consumer,
            )
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
        class CatalogueStub:
            def trusted_remote_rows(self, _sql):
                return [("content-hash", 0, {"type": None}, "")]

        with patch("materialization.executor._replace") as replace:
            _refresh_jsonld_values(CatalogueStub(), None)

        replace.assert_called_once_with(
            ANY,
            "jsonld_values",
            [],
            variant_columns={"value"},
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

    @patch("materialization.executor._materialize_html_hashes")
    @patch("materialization.executor._changed_html_hashes")
    def test_html_refresh_uses_upstream_inclusive_snapshot_bounds(
        self,
        changed_html_hashes,
        materialize_html_hashes,
    ) -> None:
        changed_html_hashes.return_value = {"changed"}
        materialize_html_hashes.return_value = (1, 0)

        _refresh_html_elements_incremental(
            MagicMock(),
            MagicMock(),
            (
                SimpleNamespace(
                    start_snapshot=40,
                    snapshot_id=42,
                    end_snapshot=43,
                ),
                SimpleNamespace(
                    start_snapshot=44,
                    snapshot_id=46,
                    end_snapshot=47,
                ),
            ),
        )

        changed_html_hashes.assert_called_once_with(
            ANY,
            start_snapshot=40,
            end_snapshot=47,
        )

    @patch("materialization.executor._apply_html_element_delta")
    @patch("materialization.executor._project_html_documents")
    @patch("materialization.executor._covered_html_hashes")
    @patch("materialization.executor._html_documents")
    def test_html_delta_projects_only_uncovered_content(
        self,
        html_documents,
        covered_html_hashes,
        project_html_documents,
        apply_html_element_delta,
    ) -> None:
        html_documents.return_value = [
            ("covered", "covered-key", "identity"),
            ("new", "new-key", "identity"),
        ]
        covered_html_hashes.return_value = {"covered", "orphaned"}
        project_html_documents.return_value = [{"content_sha256": "new"}]

        result = _materialize_html_hashes(
            MagicMock(),
            SimpleNamespace(),
            {"covered", "new", "orphaned"},
        )

        self.assertEqual(result, (1, 1))
        project_html_documents.assert_called_once_with(
            ANY,
            [("new", "new-key", "identity")],
        )
        apply_html_element_delta.assert_called_once_with(
            ANY,
            rows=[{"content_sha256": "new"}],
            removed_hashes={"orphaned"},
        )

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

    @patch("materialization.executor._replace_content_hash_slices")
    @patch("materialization.executor._changed_material_hashes")
    def test_jsonld_replaces_only_changed_hash_slices(
        self,
        changed_material_hashes,
        replace_content_hash_slices,
    ) -> None:
        catalogue = MagicMock()
        changed_material_hashes.return_value = {"hash-a"}
        catalogue.trusted_remote_rows.return_value = [
            (
                "hash-a",
                4,
                {"type": "application/ld+json"},
                '{"@type":"Article"}',
            )
        ]

        _refresh_jsonld_values_incremental(
            catalogue,
            MagicMock(),
            (
                SimpleNamespace(start_snapshot=10, end_snapshot=14),
            ),
        )

        changed_material_hashes.assert_called_once_with(
            catalogue,
            table_name="html_elements",
            start_snapshot=10,
            end_snapshot=14,
        )
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

    @patch("materialization.executor._merge_link_rows")
    @patch("materialization.executor._elements_by_hash")
    @patch("materialization.executor._changed_html_observations")
    def test_links_process_only_changed_document_observations(
        self,
        changed_html_observations,
        elements_by_hash,
        merge_link_rows,
    ) -> None:
        observed_at = datetime.fromisoformat("2026-01-02T03:04:05+00:00")
        changed_html_observations.return_value = [
            ("hash-a", "https://example.com/base", observed_at),
        ]
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

        _refresh_links_incremental(
            MagicMock(),
            MagicMock(),
            (SimpleNamespace(start_snapshot=30, end_snapshot=34),),
        )

        merge_link_rows.assert_called_once_with(
            ANY,
            [
                {
                    "source_page_id": "0b18275f-712b-5d4d-88f5-aaa0ff71b1cc",
                    "target_page_id": "6b1ac597-4b02-512d-9905-2c457bb69aa9",
                    "source_url": "https://example.com/base",
                    "target_url": "https://example.com/next",
                    "relation_scope": "same_origin",
                    "first_seen_at": observed_at,
                    "last_seen_at": observed_at,
                }
            ],
        )

    @patch("materialization.executor._elements_by_hash", return_value={})
    @patch("materialization.executor._changed_html_observations")
    def test_links_retry_when_changed_html_projection_is_not_ready(
        self,
        changed_html_observations,
        _elements_by_hash,
    ) -> None:
        changed_html_observations.return_value = [
            (
                "missing",
                "https://example.com/",
                datetime.fromisoformat("2026-01-02T03:04:05+00:00"),
            )
        ]

        with self.assertRaises(MaterializationDependencyNotReady):
            _refresh_links_incremental(
                MagicMock(),
                MagicMock(),
                (SimpleNamespace(start_snapshot=30, end_snapshot=34),),
            )

    def test_link_delta_uses_merge_timestamp_fold(self) -> None:
        catalogue = MagicMock()
        observed_at = datetime.fromisoformat("2026-01-02T03:04:05+00:00")

        _merge_link_rows(
            catalogue,
            [
                {
                    "source_page_id": "463802e4-2f8a-58f2-bac0-3f6080ec8588",
                    "target_page_id": "6b1ac597-4b02-512d-9905-2c457bb69aa9",
                    "source_url": "https://example.com/",
                    "target_url": "https://example.com/next",
                    "relation_scope": "same_origin",
                    "first_seen_at": observed_at,
                    "last_seen_at": observed_at,
                }
            ],
        )

        sql = catalogue.trusted_remote_execute.call_args.args[0]
        self.assertIn("MERGE INTO material.links", sql)
        self.assertIn("first_seen_at = least", sql)
        self.assertIn("last_seen_at = greatest", sql)
        self.assertIn("WHEN NOT MATCHED THEN INSERT", sql)
        self.assertNotIn("DELETE FROM material.links", sql)

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


if __name__ == "__main__":
    unittest.main()
