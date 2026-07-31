from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import duckdb

from atlas.platform.catalogue.client import _column_type
from atlas.platform.catalogue.exceptions import CatalogueSchemaError
from atlas.platform.catalogue.schema import expected_columns
from atlas.platform.catalogue.public import (
    PUBLIC_CATALOGUE_VERSION,
    installed_public_objects,
    install_public_catalogue,
    public_objects,
    validate_public_catalogue,
)
from atlas.materialization.registry import PROJECTIONS
from atlas.query.http import _public_metadata, metadata


class PublicCatalogueTests(unittest.TestCase):
    def test_runtime_registry_filters_removed_material_dependencies(
        self,
    ) -> None:
        without_html = tuple(
            item for item in PROJECTIONS if item.name != "html_elements"
        )
        with patch(
            "atlas.materialization.registry.PROJECTIONS",
            without_html,
        ):
            names = {
                (item.schema, item.name) for item in public_objects()
            }
        self.assertNotIn(("dom", "element"), names)
        self.assertNotIn(("dom", "content_stats"), names)
        self.assertIn(("web", "page"), names)

    def test_catalogue_uses_json_for_every_open_structured_value(self) -> None:
        column_types = {
            (relation.qualified, name): _column_type(column)
            for relation, columns in expected_columns().items()
            for name, column in columns.items()
        }

        json_columns = {
            key
            for key, data_type in column_types.items()
            if data_type == "JSON"
        }
        self.assertTrue(
            {
                ("ingest.crawls", "graph_config"),
                ("ingest.steps", "parameters"),
            }.issubset(json_columns)
        )
        self.assertNotIn("VARIANT", column_types.values())

    def test_selector_capability_requires_native_functions_and_macros(
        self,
    ) -> None:
        catalogue = _SelectorCapabilityCatalogue()

        objects = installed_public_objects(catalogue)

        self.assertIn(
            ("dom", "query_selector"),
            {(item.schema, item.name) for item in objects},
        )
        self.assertIn(
            ("dom", "query_selector_all"),
            {(item.schema, item.name) for item in objects},
        )

    def setUp(self) -> None:
        self.catalogue = _LocalCatalogue()
        for schema in ("ingest", "material"):
            self.catalogue.connection.execute(f"CREATE SCHEMA {schema}")
        for relation, columns in expected_columns().items():
            definitions = ", ".join(
                f'"{name}" {_column_type(column)}'
                for name, column in columns.items()
            )
            self.catalogue.connection.execute(
                f"CREATE TABLE {relation.qualified} ({definitions})"
            )

    def tearDown(self) -> None:
        self.catalogue.connection.close()

    def test_installs_and_validates_complete_manifest(self) -> None:
        install_public_catalogue(self.catalogue)
        validate_public_catalogue(self.catalogue)

        version, attribute = self.catalogue.connection.execute(
            "SELECT web._catalogue_version(), "
            "dom.get_attribute(MAP {'href': '/target'}, 'href')"
        ).fetchone()
        self.assertEqual(version, PUBLIC_CATALOGUE_VERSION)
        self.assertEqual(attribute, "/target")

        expected_views = sorted(
            (item.schema, item.name)
            for item in public_objects()
            if item.kind == "view"
        )
        actual_views = [
            (row[0], row[1])
            for row in self.catalogue.connection.execute(
                "SELECT schema_name, view_name FROM duckdb_views() "
                "WHERE schema_name IN ('web', 'dom') "
                "ORDER BY schema_name, view_name"
            ).fetchall()
        ]
        self.assertEqual(actual_views, expected_views)

    def test_install_removes_superseded_public_objects(self) -> None:
        self.catalogue.connection.execute("CREATE SCHEMA web")
        self.catalogue.connection.execute("CREATE SCHEMA dom")
        self.catalogue.connection.execute(
            """
            CREATE MACRO web.html_text(content_id, element_index) AS TABLE
            SELECT content_id, element_index
            """
        )
        self.catalogue.connection.execute(
            """
            CREATE MACRO dom.query_selector_all(content_id, selector) AS TABLE
            SELECT content_id, selector
            """
        )
        self.catalogue.connection.execute(
            "CREATE VIEW web.retired_html AS SELECT 1 AS element_index"
        )
        self.catalogue.connection.execute(
            "CREATE VIEW dom.retired_elements AS SELECT 1 AS element_index"
        )

        install_public_catalogue(self.catalogue)
        validate_public_catalogue(self.catalogue)

        object_names = {
            (str(row[0]), str(row[1]))
            for row in self.catalogue.connection.execute(
                """
                SELECT schema_name, function_name
                FROM duckdb_functions()
                WHERE schema_name IN ('web', 'dom')
                UNION ALL
                SELECT schema_name, view_name
                FROM duckdb_views()
                WHERE schema_name IN ('web', 'dom')
                """
            ).fetchall()
        }
        self.assertNotIn(("web", "html_text"), object_names)
        self.assertNotIn(("web", "retired_html"), object_names)
        self.assertNotIn(("dom", "retired_elements"), object_names)
        self.assertIn(("dom", "text_content"), object_names)
        self.assertIn(("dom", "element"), object_names)
        self.assertNotIn(("web", "html"), object_names)
        self.assertNotIn(("web", "attribute"), object_names)
        self.assertNotIn(("web", "text_content"), object_names)
        self.assertNotIn(("dom", "query_selector_all"), object_names)

    def test_text_content_preserves_dom_text_order(self) -> None:
        install_public_catalogue(self.catalogue)
        self.catalogue.connection.execute(
            """
            INSERT INTO material.html_elements VALUES
                ('hash', 0, NULL, 4, 0, 0, 'article', 'html', MAP {}, 'A', 'X'),
                ('hash', 1, 0,    2, 1, 0, 'b',       'html', MAP {}, 'B', 'C'),
                ('hash', 2, 0,    4, 1, 1, 'p',       'html', MAP {}, 'D', 'G'),
                ('hash', 3, 2,    4, 2, 0, 'i',       'html', MAP {}, 'E', 'F')
            """
        )

        rows = self.catalogue.connection.execute(
            """
            SELECT element.element_index, text.text_content
            FROM dom.element AS element
            JOIN LATERAL dom.text_content(
                element.content_id,
                element.element_index
            ) AS text USING (content_id, element_index)
            ORDER BY element.element_index
            """
        ).fetchall()

        self.assertEqual(
            rows,
            [(0, "ABCDEFG"), (1, "B"), (2, "DEF"), (3, "E")],
        )

    def test_dom_element_does_not_plan_subtree_reconstruction(self) -> None:
        install_public_catalogue(self.catalogue)

        plan = self.catalogue.connection.execute(
            "EXPLAIN SELECT content_id, tag_name FROM dom.element LIMIT 1"
        ).fetchone()[1]

        self.assertNotIn("direct_text", plan)
        self.assertNotIn("tail_text", plan)
        self.assertNotIn("HASH_GROUP_BY", plan)

    def test_dom_storage_is_flat_and_nested_document_api_is_absent(
        self,
    ) -> None:
        install_public_catalogue(self.catalogue)
        self.assertEqual(
            {
                row[0]
                for row in self.catalogue.connection.execute(
                    "SELECT table_name FROM duckdb_tables() "
                    "WHERE schema_name = 'material'"
                ).fetchall()
            },
            {"html_elements", "jsonld_values", "link_occurrences"},
        )
        public_names = {
            (row[0], row[1])
            for row in self.catalogue.connection.execute(
                """
                SELECT schema_name, view_name FROM duckdb_views()
                UNION ALL
                SELECT schema_name, function_name FROM duckdb_functions()
                """
            ).fetchall()
        }
        self.assertNotIn(("dom", "documents"), public_names)
        self.assertNotIn(("dom", "document"), public_names)

    def test_text_content_keeps_lateral_key_selection_bounded(self) -> None:
        install_public_catalogue(self.catalogue)

        plan = self.catalogue.connection.execute(
            """
            EXPLAIN
            WITH picked AS MATERIALIZED (
                SELECT content_id, element_index, tag_name
                FROM dom.element
                LIMIT 1
            )
            SELECT picked.content_id, picked.tag_name, text.text_content
            FROM picked
            JOIN LATERAL dom.text_content(
                picked.content_id,
                picked.element_index
            ) AS text USING (content_id, element_index)
            """
        ).fetchone()[1]

        self.assertIn("WINDOW", plan)
        self.assertIn("ROW_NUMBER()", plan)

    def test_visit_is_plain_evidence_and_page_latest_is_runtime(
        self,
    ) -> None:
        install_public_catalogue(self.catalogue)
        self.catalogue.connection.execute(
            """
            INSERT INTO ingest.visits (
                visit_id, crawl_id, requested_url, effective_url,
                admitted_at, observed_at, finished_at, outcome,
                status_code, document_id, provenance
            ) VALUES
                (
                    '10000000-0000-0000-0000-000000000001',
                    '90000000-0000-0000-0000-000000000001',
                    'https://example.com/', 'https://example.com/',
                    '2026-01-01T00:00:00Z', '2026-01-01T00:00:01Z',
                    '2026-01-01T00:00:02Z', 'success', 200,
                    '20000000-0000-0000-0000-000000000001',
                    {'kind': 'native', 'system': 'atlas',
                     'dataset': NULL, 'source_record_id': NULL}
                ),
                (
                    '10000000-0000-0000-0000-000000000002',
                    '90000000-0000-0000-0000-000000000001',
                    'https://example.com/', 'https://example.com/',
                    '2026-01-02T00:00:00Z', NULL,
                    '2026-01-02T00:00:02Z', 'failed', NULL, NULL,
                    {'kind': 'native', 'system': 'atlas',
                     'dataset': NULL, 'source_record_id': NULL}
                );

            INSERT INTO ingest.documents (
                document_id, visit_id, observed_at, representation,
                detected_media_type, content_sha256, content_bytes,
                object_key, storage_encoding, stored_bytes
            ) VALUES (
                '20000000-0000-0000-0000-000000000001',
                '10000000-0000-0000-0000-000000000001',
                '2026-01-01T00:00:01Z', 'rendered_html', 'text/html',
                'content-a', 100, 'objects/a', 'identity', 100
            );

            INSERT INTO material.html_elements VALUES
                ('content-a', 0, NULL, 5, 0, 0, 'html', 'HTML',
                 MAP {}, '', ''),
                ('content-a', 1, 0, 3, 1, 0, 'head', 'HTML',
                 MAP {}, '', ''),
                ('content-a', 2, 1, 3, 2, 0, 'script', 'HTML',
                 MAP {'type': 'application/ld+json'},
                 '{"@type":"Article"}', ''),
                ('content-a', 3, 0, 5, 1, 1, 'body', 'HTML',
                 MAP {}, '', ''),
                ('content-a', 4, 3, 5, 2, 0, 'a', 'HTML',
                 MAP {'href': '/next'}, 'Next', '');

            INSERT INTO material.jsonld_values VALUES (
                'content-a', 2, ['Article'], '{"@type":"Article"}'
            );

            INSERT INTO material.link_occurrences VALUES (
                '30000000-0000-0000-0000-000000000001',
                '40000000-0000-0000-0000-000000000001',
                '10000000-0000-0000-0000-000000000001',
                '20000000-0000-0000-0000-000000000001',
                'content-a', 4, '2026-01-01T00:00:01Z', '/next',
                'https://example.com/', 'https://example.com/next',
                'same_origin'
            );
            """
        )

        visits = self.catalogue.connection.execute(
            """
            SELECT page_visit_id::VARCHAR, outcome, content_id
            FROM web.page_visit
            ORDER BY page_visit_id
            """
        ).fetchall()
        self.assertEqual(
            visits,
            [
                (
                    "10000000-0000-0000-0000-000000000001",
                    "success",
                    "content-a",
                ),
                (
                    "10000000-0000-0000-0000-000000000002",
                    "failed",
                    None,
                ),
            ],
        )
        page = self.catalogue.connection.execute(
            """
            SELECT url, scheme, hostname, port, path, query_string,
                   latest_page_visit_id::VARCHAR, last_visited_at
            FROM web.page
            """
        ).fetchone()
        self.assertEqual(page[0], "https://example.com/")
        self.assertEqual(page[1:6], ("https", "example.com", None, "/", None))
        self.assertEqual(
            page[6],
            "10000000-0000-0000-0000-000000000002",
        )
        self.assertEqual(
            self.catalogue.connection.execute(
                "SELECT element_count, max_depth FROM dom.content_stats"
            ).fetchone(),
            (5, 2),
        )
        self.assertEqual(
            self.catalogue.connection.execute(
                "SELECT content_id, element_index, type_terms, value "
                "FROM web.jsonld"
            ).fetchone(),
            (
                "content-a",
                2,
                ["Article"],
                '{"@type":"Article"}',
            ),
        )
        self.assertEqual(
            self.catalogue.connection.execute(
                "SELECT page_visit_id::VARCHAR, document_id::VARCHAR, "
                "content_id, element_index, source_hostname, "
                "target_hostname, relationship "
                "FROM web.link_occurrence"
            ).fetchone(),
            (
                "10000000-0000-0000-0000-000000000001",
                "20000000-0000-0000-0000-000000000001",
                "content-a",
                4,
                "example.com",
                "example.com",
                "same_origin",
            ),
        )
        self.assertEqual(
            self.catalogue.connection.execute(
                "SELECT page_visit_count, content_count, "
                "occurrence_count FROM web.link"
            ).fetchone(),
            (1, 1, 1),
        )

    def test_validation_rejects_unexpected_public_object(self) -> None:
        install_public_catalogue(self.catalogue)
        self.catalogue.connection.execute(
            "CREATE VIEW dom.unmanaged AS SELECT 1 AS value"
        )

        with self.assertRaisesRegex(
            CatalogueSchemaError,
            "unmanaged",
        ):
            validate_public_catalogue(self.catalogue)

    def test_install_publishes_portable_view_comments(self) -> None:
        install_public_catalogue(self.catalogue)
        install_public_catalogue(self.catalogue)

        view_comment = self.catalogue.connection.execute(
            """
            SELECT comment
            FROM duckdb_views()
            WHERE schema_name = 'web'
              AND view_name = 'page_visit'
            """
        ).fetchone()[0]
        dom_comment = self.catalogue.connection.execute(
            """
            SELECT comment
            FROM duckdb_views()
            WHERE schema_name = 'dom'
              AND view_name = 'element'
            """
        ).fetchone()[0]

        self.assertEqual(
            view_comment,
            "Page-visit history with retained document evidence.",
        )
        self.assertEqual(
            dom_comment,
            "Structural DOM elements keyed by immutable content.",
        )

    def test_validation_rejects_stale_public_comments(self) -> None:
        install_public_catalogue(self.catalogue)
        self.catalogue.connection.execute(
            "COMMENT ON VIEW web.page IS 'stale'"
        )

        with self.assertRaises(CatalogueSchemaError) as raised:
            validate_public_catalogue(self.catalogue)

        self.assertIn(
            "web.page: missing or stale view comment",
            str(raised.exception),
        )

    def test_shell_metadata_includes_public_macro_signatures(self) -> None:
        install_public_catalogue(self.catalogue)

        version, duckdb_version, catalogue_bytes, rows, macro_rows = (
            _public_metadata(self.catalogue)
        )

        self.assertEqual(version, PUBLIC_CATALOGUE_VERSION)
        self.assertEqual(duckdb_version, "v-test")
        self.assertEqual(catalogue_bytes, 12_345)
        self.assertIn(
            (
                "web",
                "page",
                "Canonical normalized URL identities observed through page visits.",
                "url",
                "VARCHAR",
                True,
                "Unique normalized URL represented by this page.",
            ),
            rows,
        )
        self.assertIn(
            (
                "dom",
                "element",
                "Structural DOM elements keyed by immutable content.",
                "content_id",
                "VARCHAR",
                True,
                "Immutable HTML content identity.",
            ),
            rows,
        )
        self.assertNotIn(("web", "page_history"), macro_rows)
        self.assertNotIn(("web", "link_history"), macro_rows)
        self.assertIn(("dom", "text_content"), macro_rows)

    def test_shell_metadata_response_separates_views_and_macros(self) -> None:
        install_public_catalogue(self.catalogue)

        response = asyncio.run(metadata(_LocalCatalogueControl(self.catalogue)))

        self.assertEqual(
            response.catalogue_version,
            PUBLIC_CATALOGUE_VERSION,
        )
        self.assertEqual(response.duckdb_version, "v-test")
        self.assertEqual(response.catalogue_bytes, 12_345)
        relations = {
            (item.schema_name, item.name): item
            for item in response.relations
        }
        self.assertIn(("web", "page"), relations)
        self.assertIn(("dom", "element"), relations)
        self.assertEqual(
            relations[("web", "page")].description,
            "Canonical normalized URL identities observed through page visits.",
        )
        self.assertEqual(
            relations[("web", "page")].columns[0].description,
            "Unique normalized URL represented by this page.",
        )
        macros = {
            (item.schema_name, item.name): item
            for item in response.macros
        }
        self.assertEqual(
            macros[("dom", "get_attribute")].kind,
            "scalar_macro",
        )
        self.assertEqual(
            macros[("dom", "get_attribute")].parameters[0].data_type,
            "MAP(VARCHAR, VARCHAR)",
        )
        self.assertEqual(
            macros[("dom", "text_content")].kind,
            "table_macro",
        )
        self.assertNotIn(("web", "page_history"), macros)
        self.assertNotIn(("web", "link_history"), macros)
        self.assertNotIn(("dom", "query_selector"), macros)
        self.assertNotIn(("dom", "query_selector_all"), macros)


class _LocalCatalogue:
    def __init__(self) -> None:
        self.connection = duckdb.connect()
        self.config = SimpleNamespace(alias="atlas")

    @contextmanager
    def remote_transaction(self):
        self.connection.execute("BEGIN TRANSACTION")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def trusted_remote_execute(self, sql: str) -> list[tuple]:
        self.connection.execute(sql)
        return []

    def trusted_remote_rows(self, sql: str) -> list[tuple]:
        if "ducklake_table_info" in sql:
            return [("v-test", 12_345)]
        return self.connection.execute(sql).fetchall()


class _SelectorCapabilityCatalogue:
    def trusted_remote_rows(self, sql: str) -> list[tuple]:
        if "DISTINCT function_name" in sql:
            return [
                ("atlas_dom_select_first_keyed",),
                ("atlas_dom_select_all_keyed",),
            ]
        if "function_type = 'table_macro'" in sql:
            return [
                ("dom", "query_selector"),
                ("dom", "query_selector_all"),
            ]
        raise AssertionError(sql)


class _LocalCatalogueControl:
    def __init__(self, catalogue: _LocalCatalogue) -> None:
        self.catalogue = catalogue

    async def run(self, operation):
        return operation(None, self.catalogue)


if __name__ == "__main__":
    unittest.main()
