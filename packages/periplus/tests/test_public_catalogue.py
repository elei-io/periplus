from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
import unittest

import duckdb

from periplus.platform.catalogue.client import _column_type
from periplus.platform.catalogue.exceptions import CatalogueSchemaError
from periplus.platform.catalogue.public import (
    PUBLIC_CATALOGUE_VERSION,
    install_public_catalogue,
    public_objects,
    validate_public_catalogue,
)
from periplus.platform.catalogue.schema import expected_columns
from periplus.query.http import _public_metadata, metadata


EXPECTED_PUBLIC_RELATIONS = {
    ("public_v1", "prose"),
    ("public_v1", "term"),
    ("public_v1", "term_node"),
    ("public_v1", "capture"),
    ("public_v1", "link"),
    ("public_v1", "html_element"),
    ("public_v1", "html_node"),
    ("public_v1", "html_heading"),
    ("public_v1", "html_code"),
    ("public_v1", "html_section"),
    ("public_v1", "html_metadata"),
    ("public_v1", "html_image"),
    ("public_v1", "html_jsonld"),
    ("public_v1", "html_list"),
    ("public_v1", "html_form"),
    ("public_v1", "html_form_control"),
    ("public_v1", "html_select_option"),
    ("public_v1", "html_list_item"),
    ("public_v1", "html_table"),
    ("public_v1", "html_table_cell"),
}


class PublicCatalogueTests(unittest.TestCase):
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

    def test_manifest_is_exactly_the_narrow_public_contract(self) -> None:
        objects = public_objects()
        self.assertEqual(
            {(item.schema, item.name) for item in objects if item.kind == "view"},
            EXPECTED_PUBLIC_RELATIONS,
        )
        self.assertEqual([(item.schema, item.name) for item in objects if item.kind == "table_macro"], [("public_v1", "subtree_text")])
        self.assertTrue(all(not item.requires_functions for item in objects))

    def test_installs_and_validates_public_views(self) -> None:
        install_public_catalogue(self.catalogue)
        validate_public_catalogue(self.catalogue)

        views = {
            (str(schema), str(name))
            for schema, name in self.catalogue.connection.execute(
                "SELECT schema_name, view_name FROM duckdb_views() "
                "WHERE schema_name IN ('public_v1', 'web', 'content', 'dom')"
            ).fetchall()
        }
        macros = self.catalogue.connection.execute(
            "SELECT function_name FROM duckdb_functions() "
            "WHERE schema_name IN ('public_v1', 'web', 'content', 'dom') "
            "AND function_type IN ('macro', 'table_macro')"
        ).fetchall()
        self.assertEqual(views, EXPECTED_PUBLIC_RELATIONS)
        with self.assertRaises(duckdb.CatalogException):
            self.catalogue.connection.execute("SELECT * FROM public_v1.object")
        self.assertEqual(macros, [("subtree_text",)])

    def test_install_removes_superseded_web_and_dom_objects(self) -> None:
        self.catalogue.connection.execute("CREATE SCHEMA web")
        self.catalogue.connection.execute("CREATE SCHEMA dom")
        self.catalogue.connection.execute(
            "CREATE VIEW web.page AS SELECT 'https://example.com/' AS url"
        )
        self.catalogue.connection.execute(
            "CREATE MACRO web._catalogue_version() AS 'old'"
        )
        self.catalogue.connection.execute(
            "CREATE VIEW dom.element AS SELECT 1 AS element_index"
        )

        install_public_catalogue(self.catalogue)
        validate_public_catalogue(self.catalogue)

        schemas = {
            str(row[0])
            for row in self.catalogue.connection.execute(
                "SELECT schema_name FROM duckdb_schemas()"
            ).fetchall()
        }
        self.assertNotIn("dom", schemas)
        self.assertEqual(
            self.catalogue.connection.execute(
                "SELECT count(*) FROM duckdb_views() "
                "WHERE schema_name = 'web' AND view_name = 'page'"
            ).fetchone()[0],
            0,
        )

    def test_install_replaces_public_link_occurrence(self) -> None:
        self.catalogue.connection.execute("CREATE SCHEMA public_v1")
        self.catalogue.connection.execute(
            "CREATE VIEW public_v1.link_occurrence AS SELECT 1 AS old_column"
        )
        install_public_catalogue(self.catalogue)
        validate_public_catalogue(self.catalogue)
        with self.assertRaises(duckdb.CatalogException):
            self.catalogue.connection.execute("SELECT * FROM public_v1.link_occurrence")
        self.assertEqual(
            [row[0] for row in self.catalogue.connection.execute("DESCRIBE public_v1.link").fetchall()],
            ["capture_id", "node_index", "raw_href", "resolved_url"],
        )

    def test_views_preserve_observation_content_and_occurrence_grains(self) -> None:
        install_public_catalogue(self.catalogue)
        self.catalogue.connection.execute(
            """
            INSERT INTO ingest.visits (
                visit_id, requested_url, effective_url,
                admitted_at, observed_at, finished_at, outcome,
                status_code, document_id
            ) VALUES
                (
                    '10000000-0000-0000-0000-000000000001',
                    'https://example.com/start', 'https://example.com/',
                    '2026-01-01T00:00:00Z', '2026-01-01T00:00:01Z',
                    '2026-01-01T00:00:02Z', 'success', 200,
                    '20000000-0000-0000-0000-000000000001'
                ),
                (
                    '10000000-0000-0000-0000-000000000002',
                    'https://example.com/missing', NULL,
                    '2026-01-02T00:00:00Z', NULL,
                    '2026-01-02T00:00:02Z', 'failed', NULL, NULL
                ),
                (
                    '10000000-0000-0000-0000-000000000003',
                    'https://mirror.example/', 'https://mirror.example/',
                    '2026-01-03T00:00:00Z', '2026-01-03T00:00:01Z',
                    '2026-01-03T00:00:02Z', 'success', 200,
                    '20000000-0000-0000-0000-000000000003'
                );

            INSERT INTO ingest.documents (
                document_id, visit_id, observed_at, representation,
                detected_media_type, charset, content_sha256, content_bytes,
                object_key, storage_encoding, stored_bytes
            ) VALUES
                (
                    '20000000-0000-0000-0000-000000000001',
                    '10000000-0000-0000-0000-000000000001',
                    '2026-01-01T00:00:01Z', 'rendered_html', 'text/html',
                    'utf-8', 'content-a', 100, 'objects/a', 'identity', 100
                ),
                (
                    '20000000-0000-0000-0000-000000000003',
                    '10000000-0000-0000-0000-000000000003',
                    '2026-01-03T00:00:01Z', 'response_body', 'text/html',
                    'utf-8', 'content-a', 100, 'objects/a', 'identity', 100
                );

            INSERT INTO material.html_nodes (content_sha256,node_index,parent_index,subtree_end_index,depth,sibling_index,name,tag,namespace,attributes,text_direct,node_type) VALUES
                ('content-a', 0, NULL, 2, 0, 0, 'html', 'html', 'http://www.w3.org/1999/xhtml',
                 MAP {}, '', 'element'),
                ('content-a', 1, 0, 2, 1, 0, 'a', 'a', 'http://www.w3.org/1999/xhtml',
                 MAP {'href': '/next'}, 'Next', 'element');

            INSERT INTO material.link_occurrences VALUES (
                '30000000-0000-0000-0000-000000000001',
                '40000000-0000-0000-0000-000000000001',
                '10000000-0000-0000-0000-000000000001',
                '20000000-0000-0000-0000-000000000001',
                'content-a', 1, '2026-01-01T00:00:01Z', '/next',
                'https://example.com/', 'https://example.com/next',
                'same_origin'
            );
            """
        )

        observations = self.catalogue.connection.execute(
            "SELECT capture_id::VARCHAR, requested_url, effective_url, "
            "content_id FROM public_v1.capture ORDER BY capture_id"
        ).fetchall()
        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[0][3], "content-a")
        self.assertEqual(observations[1][3], "content-a")

        self.assertEqual(
            self.catalogue.connection.execute(
                "SELECT content_id, byte_length "
                "FROM public_v1.capture ORDER BY capture_id"
            ).fetchall(),
            [("content-a", 100), ("content-a", 100)],
        )
        self.assertEqual(
            self.catalogue.connection.execute(
                "SELECT content_id, node_index, parent_index, tag, "
                "text_direct, depth FROM public_v1.html_element ORDER BY node_index"
            ).fetchall(),
            [
                ("content-a", 0, None, "html", "", 0),
                ("content-a", 1, 0, "a", "Next", 1),
            ],
        )
        self.assertEqual(
            self.catalogue.connection.execute(
                "SELECT capture_id::VARCHAR, node_index, raw_href, resolved_url "
                "FROM public_v1.link"
            ).fetchone(),
            ("10000000-0000-0000-0000-000000000001", 1, "/next", "https://example.com/next"),
        )

    def test_shared_content_is_available_from_every_observation(self) -> None:
        self.test_views_preserve_observation_content_and_occurrence_grains()
        connection = self.catalogue.connection
        self.assertEqual(connection.execute("SELECT count(*) FROM public_v1.capture").fetchone(), (2,))
        self.assertEqual(connection.execute("SELECT count(*) FROM public_v1.capture JOIN public_v1.html_element USING (content_id)").fetchone(), (4,))

    def test_capture_exposes_only_html_without_format_columns(self) -> None:
        install_public_catalogue(self.catalogue)
        rows = [
            ("text/html", "html"),
            ("TEXT/HTML", "html"),
            ("application/xhtml+xml", "xml"),
            ("application/json", "json"),
            ("application/ld+json", "json"),
            ("application/pdf", "pdf"),
            ("image/png", "image"),
            ("application/xml", "xml"),
            ("text/plain", "text"),
            ("application/octet-stream", "binary"),
        ]
        for index, (media_type, _format) in enumerate(rows, start=1):
            self.catalogue.connection.execute(
                "INSERT INTO ingest.visits (visit_id, requested_url, admitted_at, "
                "finished_at, outcome, document_id) "
                "VALUES (?, 'https://example.com/', now(), now(), 'succeeded', ?)",
                [f"10000000-0000-0000-0000-{index:012d}",
                 f"00000000-0000-0000-0000-{index:012d}"],
            )
            self.catalogue.connection.execute(
                "INSERT INTO ingest.documents (document_id, visit_id, "
                "observed_at, representation, detected_media_type, "
                "content_sha256, content_bytes, object_key, storage_encoding, "
                "stored_bytes) VALUES (?, ?, now(), 'response_body', ?, ?, 1, "
                "?, 'identity', 1)",
                [
                    f"00000000-0000-0000-0000-{index:012d}",
                    f"10000000-0000-0000-0000-{index:012d}",
                    media_type,
                    f"content-{index}",
                    f"objects/{index}",
                ],
            )
        self.assertEqual(
            self.catalogue.connection.execute(
                "SELECT content_id FROM public_v1.capture ORDER BY content_id"
            ).fetchall(),
            [("content-1",), ("content-2",)],
        )

        columns = {row[0] for row in self.catalogue.connection.execute("DESCRIBE public_v1.capture").fetchall()}
        self.assertNotIn("media_type", columns)
        self.assertNotIn("representation", columns)
        # Retained HTML error bodies and rendered HTML remain public before DOM generation.
        self.catalogue.connection.execute("UPDATE ingest.visits SET status_code=404")
        self.catalogue.connection.execute("UPDATE ingest.documents SET representation='rendered_html' WHERE content_sha256='content-2'")
        self.assertEqual(self.catalogue.connection.execute("SELECT count(*) FROM public_v1.capture WHERE http_status_code=404").fetchone(), (2,))

    def test_validation_rejects_unexpected_public_object(self) -> None:
        install_public_catalogue(self.catalogue)
        self.catalogue.connection.execute(
            "CREATE VIEW public_v1.unmanaged AS SELECT 1 AS value"
        )
        with self.assertRaisesRegex(CatalogueSchemaError, "unmanaged"):
            validate_public_catalogue(self.catalogue)

    def test_metadata_exposes_relations_and_registered_helpers(self) -> None:
        install_public_catalogue(self.catalogue)
        version, duckdb_version, catalogue_bytes, rows, macro_rows = (
            _public_metadata(self.catalogue)
        )
        self.assertEqual(version, PUBLIC_CATALOGUE_VERSION)
        self.assertEqual(duckdb_version, "v-test")
        self.assertEqual(catalogue_bytes, 12_345)
        self.assertEqual(
            {(str(row[0]), str(row[1])) for row in rows},
            EXPECTED_PUBLIC_RELATIONS,
        )
        self.assertEqual(set(macro_rows), {("public_v1", "subtree_text")})

        response = asyncio.run(metadata(_LocalCatalogueControl(self.catalogue)))
        self.assertEqual(response.catalogue_version, PUBLIC_CATALOGUE_VERSION)
        self.assertEqual(
            {(item.schema_name, item.name) for item in response.relations},
            EXPECTED_PUBLIC_RELATIONS,
        )
        self.assertEqual([item.name for item in response.macros], ["subtree_text"])


class _LocalCatalogue:
    def __init__(self) -> None:
        self.connection = duckdb.connect()
        self.config = SimpleNamespace(alias="periplus")

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


class _LocalCatalogueControl:
    def __init__(self, catalogue: _LocalCatalogue) -> None:
        self.catalogue = catalogue

    async def run(self, operation):
        return operation(None, self.catalogue)


if __name__ == "__main__":
    unittest.main()
