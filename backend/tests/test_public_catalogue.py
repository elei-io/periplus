from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import unittest

import duckdb

from atlas.platform.catalogue.client import _column_type
from atlas.platform.catalogue.exceptions import CatalogueSchemaError
from atlas.platform.catalogue.schema import expected_columns
from atlas.platform.catalogue.public import (
    PUBLIC_CATALOGUE_VERSION,
    PUBLIC_OBJECTS,
    install_public_catalogue,
    validate_public_catalogue,
)
from atlas.query.http import _public_metadata, metadata


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
            for item in PUBLIC_OBJECTS
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
        self.assertIn(("dom", "elements"), object_names)
        self.assertNotIn(("web", "html"), object_names)
        self.assertNotIn(("web", "attribute"), object_names)
        self.assertNotIn(("web", "text_content"), object_names)

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
            FROM dom.elements AS element
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

    def test_dom_elements_does_not_plan_subtree_reconstruction(self) -> None:
        install_public_catalogue(self.catalogue)

        plan = self.catalogue.connection.execute(
            "EXPLAIN SELECT content_id, tag FROM dom.elements LIMIT 1"
        ).fetchone()[1]

        self.assertNotIn("text_direct", plan)
        self.assertNotIn("text_tail", plan)
        self.assertNotIn("HASH_GROUP_BY", plan)

    def test_dom_documents_exposes_costing_statistics_not_physical_nodes(
        self,
    ) -> None:
        install_public_catalogue(self.catalogue)
        self.catalogue.connection.execute(
            """
            INSERT INTO material.html_documents VALUES (
                'hash',
                4,
                2,
                []::STRUCT(
                    element_index INTEGER,
                    parent_index INTEGER,
                    subtree_end_index INTEGER,
                    depth INTEGER,
                    child_index INTEGER,
                    tag VARCHAR,
                    namespace VARCHAR,
                    attributes MAP(VARCHAR, VARCHAR),
                    text_direct VARCHAR,
                    text_tail VARCHAR
                )[]
            )
            """
        )

        description = self.catalogue.connection.execute(
            "DESCRIBE dom.documents"
        ).fetchall()
        row = self.catalogue.connection.execute(
            "SELECT * FROM dom.documents"
        ).fetchone()

        self.assertEqual(
            [column[0] for column in description],
            ["content_id", "node_count", "max_depth"],
        )
        self.assertEqual(row, ("hash", 4, 2))

        keyed = self.catalogue.connection.execute(
            "SELECT content_id, node_count, len(nodes) "
            "FROM dom.document('hash')"
        ).fetchone()
        self.assertEqual(keyed, ("hash", 4, 0))

    def test_text_content_keeps_lateral_key_selection_bounded(self) -> None:
        install_public_catalogue(self.catalogue)

        plan = self.catalogue.connection.execute(
            """
            EXPLAIN
            WITH picked AS MATERIALIZED (
                SELECT content_id, element_index, tag
                FROM dom.elements
                LIMIT 1
            )
            SELECT picked.content_id, picked.tag, text.text_content
            FROM picked
            JOIN LATERAL dom.text_content(
                picked.content_id,
                picked.element_index
            ) AS text USING (content_id, element_index)
            """
        ).fetchone()[1]

        self.assertIn("WINDOW", plan)
        self.assertIn("ROW_NUMBER()", plan)

    def test_page_stats_reduces_observations_and_unique_link_pairs(
        self,
    ) -> None:
        install_public_catalogue(self.catalogue)
        self.catalogue.connection.execute(
            """
            INSERT INTO material.pages (
                page_id,
                normalized_url,
                scheme,
                hostname,
                path
            )
            VALUES
                ('00000000-0000-0000-0000-000000000001',
                 'https://example.com/a',
                 'https',
                 'example.com',
                 '/a'),
                ('00000000-0000-0000-0000-000000000002',
                 'https://example.com/b',
                 'https',
                 'example.com',
                 '/b'),
                ('00000000-0000-0000-0000-000000000003',
                 'https://example.com/c',
                 'https',
                 'example.com',
                 '/c');

            INSERT INTO material.page_observations
                (page_id, visit_id, document_id, observed_at)
            VALUES
                ('00000000-0000-0000-0000-000000000001',
                 '10000000-0000-0000-0000-000000000001',
                 '20000000-0000-0000-0000-000000000001',
                 '2026-01-01T00:00:00Z'),
                ('00000000-0000-0000-0000-000000000001',
                 '10000000-0000-0000-0000-000000000002',
                 '20000000-0000-0000-0000-000000000002',
                 '2026-01-02T00:00:00Z'),
                ('00000000-0000-0000-0000-000000000001',
                 '10000000-0000-0000-0000-000000000003',
                 '20000000-0000-0000-0000-000000000003',
                 '2026-01-03T00:00:00Z'),
                ('00000000-0000-0000-0000-000000000002',
                 '10000000-0000-0000-0000-000000000004',
                 NULL,
                 '2026-01-04T00:00:00Z');

            INSERT INTO ingest.documents (
                document_id,
                visit_id,
                observed_at,
                representation,
                detected_media_type,
                content_sha256,
                content_bytes,
                object_key,
                storage_encoding,
                stored_bytes
            )
            VALUES
                ('20000000-0000-0000-0000-000000000001',
                 '10000000-0000-0000-0000-000000000001',
                 '2026-01-01T00:00:00Z',
                 'rendered_html',
                 'text/html',
                 'content-a',
                 1,
                 'objects/a',
                 'identity',
                 1),
                ('20000000-0000-0000-0000-000000000002',
                 '10000000-0000-0000-0000-000000000002',
                 '2026-01-02T00:00:00Z',
                 'rendered_html',
                 'text/html',
                 'content-a',
                 1,
                 'objects/a',
                 'identity',
                 1),
                ('20000000-0000-0000-0000-000000000003',
                 '10000000-0000-0000-0000-000000000003',
                 '2026-01-03T00:00:00Z',
                 'rendered_html',
                 'text/html',
                 'content-b',
                 1,
                 'objects/b',
                 'identity',
                 1);

            INSERT INTO material.links (
                link_id,
                source_page_id,
                target_page_id,
                source_url,
                target_url,
                relation_scope
            )
            VALUES
                ('30000000-0000-0000-0000-000000000001',
                 '00000000-0000-0000-0000-000000000001',
                 '00000000-0000-0000-0000-000000000001',
                 'https://example.com/a',
                 'https://example.com/a',
                 'self'),
                ('30000000-0000-0000-0000-000000000002',
                 '00000000-0000-0000-0000-000000000001',
                 '00000000-0000-0000-0000-000000000002',
                 'https://example.com/a',
                 'https://example.com/b',
                 'same_origin'),
                ('30000000-0000-0000-0000-000000000003',
                 '00000000-0000-0000-0000-000000000002',
                 '00000000-0000-0000-0000-000000000001',
                 'https://example.com/b',
                 'https://example.com/a',
                 'same_origin');
            """
        )

        rows = self.catalogue.connection.execute(
            """
            SELECT
                page_id::VARCHAR,
                visit_count,
                document_count,
                distinct_content_count,
                first_observed_at,
                last_observed_at,
                inbound_link_count,
                outbound_link_count
            FROM web.page_stats
            ORDER BY page_id
            """
        ).fetchall()

        self.assertEqual(
            rows,
            [
                (
                    "00000000-0000-0000-0000-000000000001",
                    3,
                    3,
                    2,
                    datetime(2026, 1, 1, tzinfo=timezone.utc),
                    datetime(2026, 1, 3, tzinfo=timezone.utc),
                    2,
                    2,
                ),
                (
                    "00000000-0000-0000-0000-000000000002",
                    1,
                    0,
                    0,
                    datetime(2026, 1, 4, tzinfo=timezone.utc),
                    datetime(2026, 1, 4, tzinfo=timezone.utc),
                    1,
                    1,
                ),
                (
                    "00000000-0000-0000-0000-000000000003",
                    0,
                    0,
                    0,
                    None,
                    None,
                    0,
                    0,
                ),
            ],
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
              AND view_name = 'page_stats'
            """
        ).fetchone()[0]
        dom_comment = self.catalogue.connection.execute(
            """
            SELECT comment
            FROM duckdb_views()
            WHERE schema_name = 'dom'
              AND view_name = 'elements'
            """
        ).fetchone()[0]

        self.assertEqual(
            view_comment,
            "Observation and directed-link evidence summarized by page.",
        )
        self.assertEqual(
            dom_comment,
            "Structural elements projected from immutable HTML content.",
        )

    def test_validation_rejects_stale_public_comments(self) -> None:
        install_public_catalogue(self.catalogue)
        self.catalogue.connection.execute(
            "COMMENT ON VIEW web.pages IS 'stale'"
        )

        with self.assertRaises(CatalogueSchemaError) as raised:
            validate_public_catalogue(self.catalogue)

        self.assertIn(
            "web.pages: missing or stale view comment",
            str(raised.exception),
        )

    def test_shell_metadata_includes_public_macro_signatures(self) -> None:
        install_public_catalogue(self.catalogue)

        version, rows, macro_rows = _public_metadata(self.catalogue)

        self.assertEqual(version, PUBLIC_CATALOGUE_VERSION)
        self.assertIn(
            (
                "web",
                "pages",
                "Normalized page identities observed through visits.",
                "page_id",
                "UUID",
                True,
                "Deterministic identity derived from the normalized URL.",
            ),
            rows,
        )
        self.assertIn(
            (
                "dom",
                "elements",
                "Structural elements projected from immutable HTML content.",
                "content_id",
                "VARCHAR",
                True,
                "Identity of the projected immutable HTML bytes.",
            ),
            rows,
        )
        self.assertEqual(
            [row[0] for row in macro_rows[("web", "page_history")]],
            list(
                next(
                    item.columns
                    for item in PUBLIC_OBJECTS
                    if item.schema == "web" and item.name == "page_history"
                )
            ),
        )
        self.assertEqual(
            next(
                item.parameters
                for item in PUBLIC_OBJECTS
                if item.schema == "web" and item.name == "link_history"
            ),
            (("selected_link_id", "UUID"),),
        )

    def test_shell_metadata_response_separates_views_and_macros(self) -> None:
        install_public_catalogue(self.catalogue)

        response = asyncio.run(metadata(_LocalCatalogueControl(self.catalogue)))

        self.assertEqual(
            response.catalogue_version,
            PUBLIC_CATALOGUE_VERSION,
        )
        relations = {
            (item.schema_name, item.name): item
            for item in response.relations
        }
        self.assertIn(("web", "pages"), relations)
        self.assertIn(("dom", "elements"), relations)
        self.assertEqual(
            relations[("web", "pages")].description,
            "Normalized page identities observed through visits.",
        )
        self.assertEqual(
            relations[("web", "pages")].columns[0].description,
            "Deterministic identity derived from the normalized URL.",
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
            macros[("web", "page_history")].kind,
            "table_macro",
        )
        self.assertEqual(
            macros[("dom", "text_content")].kind,
            "table_macro",
        )
        self.assertIn(
            "content_id",
            {
                column.name
                for column in macros[("web", "page_history")].columns
            },
        )


class _LocalCatalogue:
    def __init__(self) -> None:
        self.connection = duckdb.connect()

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
        return self.connection.execute(sql).fetchall()


class _LocalCatalogueControl:
    def __init__(self, catalogue: _LocalCatalogue) -> None:
        self.catalogue = catalogue

    async def run(self, operation):
        return operation(None, self.catalogue)


if __name__ == "__main__":
    unittest.main()
