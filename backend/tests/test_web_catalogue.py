from __future__ import annotations

from contextlib import contextmanager
import unittest

import duckdb

from atlas.platform.catalogue.client import _column_type
from atlas.platform.catalogue.exceptions import CatalogueSchemaError
from atlas.platform.catalogue.schema import expected_columns
from atlas.platform.catalogue.web import (
    WEB_CATALOGUE_VERSION,
    WEB_OBJECTS,
    install_web_catalogue,
    validate_web_catalogue,
)


class WebCatalogueTests(unittest.TestCase):
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
        install_web_catalogue(self.catalogue)
        validate_web_catalogue(self.catalogue)

        version, attribute = self.catalogue.connection.execute(
            "SELECT web._catalogue_version(), "
            "web.attribute(MAP {'href': '/target'}, 'href')"
        ).fetchone()
        self.assertEqual(version, WEB_CATALOGUE_VERSION)
        self.assertEqual(attribute, "/target")

        expected_views = sorted(
            item.name for item in WEB_OBJECTS if item.kind == "view"
        )
        actual_views = [
            row[0]
            for row in self.catalogue.connection.execute(
                "SELECT view_name FROM duckdb_views() "
                "WHERE schema_name = 'web' ORDER BY view_name"
            ).fetchall()
        ]
        self.assertEqual(actual_views, expected_views)

    def test_html_text_content_preserves_dom_text_order(self) -> None:
        install_web_catalogue(self.catalogue)
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
            "SELECT element_index, text_content "
            "FROM web.html ORDER BY element_index"
        ).fetchall()

        self.assertEqual(
            rows,
            [(0, "ABCDEFG"), (1, "B"), (2, "DEF"), (3, "E")],
        )

    def test_validation_rejects_unexpected_public_object(self) -> None:
        install_web_catalogue(self.catalogue)
        self.catalogue.connection.execute(
            "CREATE VIEW web.unmanaged AS SELECT 1 AS value"
        )

        with self.assertRaisesRegex(
            CatalogueSchemaError,
            "unmanaged",
        ):
            validate_web_catalogue(self.catalogue)


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


if __name__ == "__main__":
    unittest.main()
