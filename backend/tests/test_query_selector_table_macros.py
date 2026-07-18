from __future__ import annotations

from dataclasses import asdict
import tempfile
import unittest
from pathlib import Path

import duckdb
from ducklake_client import DiskStorage, DuckDBCatalog
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from control.catalogue_fixtures import seed_catalogue_fixtures
from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_queries.models import CatalogueQuery, CatalogueQueryRevision
from control.catalogue_scalar_macros.models import CatalogueScalarMacroDefinition
from control.catalogue_table_macros.models import CatalogueTableMacroDefinition
from control.catalogue_views.models import CatalogueViewReference
from db import Base
from dom import encode_html
from repository.catalogue import Catalogue, CatalogueConfig


FIXTURES = Path(__file__).parents[2] / "fixtures"
HTML = """
<!doctype html>
<html>
  <body>
    <main id="main">
      <article id="first" class="card featured" data-locale="en-US">
        <a id="first-link" href="/guide.pdf">Guide</a>
      </article>
      <article id="second" class="card">
        <a id="second-link" href="/details">Details</a>
      </article>
      <aside id="aside"><p id="empty"></p></aside>
    </main>
  </body>
</html>
"""


class QuerySelectorTableMacroTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            self.engine,
            tables=[
                CatalogueQuery.__table__,
                CatalogueQueryRevision.__table__,
                CatalogueViewReference.__table__,
                CatalogueMaterialization.__table__,
                CatalogueTableMacroDefinition.__table__,
                CatalogueScalarMacroDefinition.__table__,
            ],
        )
        self.session = Session(self.engine, expire_on_commit=False)
        self.catalogue = Catalogue(
            CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
        )
        self.catalogue.bootstrap()
        seed_catalogue_fixtures(self.session, self.catalogue, FIXTURES)
        rows = [
            {"document_id": document_id, **asdict(row)}
            for document_id in ("doc-one", "doc-two")
            for row in encode_html(HTML)
        ]
        self.catalogue.lake.table.append("elements", rows, schema_name="main")

    def tearDown(self) -> None:
        self.catalogue.close()
        self.session.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _matches(
        self, selector: str, document_id: str | None = "doc-one"
    ) -> list[tuple[str, str | None]]:
        arguments = "?, ?" if document_id is not None else "?"
        parameters = [selector, document_id] if document_id is not None else [selector]
        return self.catalogue.connection.execute(
            f"""
            SELECT
                document_id,
                atlas.macros.get_attribute(attributes, 'id') AS id
            FROM atlas.macros.query_selector_all({arguments})
            WHERE id IS NOT NULL
            ORDER BY document_id, element_index
            """,
            parameters,
        ).fetchall()

    def test_supported_selector_subset(self) -> None:
        cases = {
            "article.card": ["first", "second"],
            "#first.featured": ["first"],
            "article > a[href]": ["first-link", "second-link"],
            "main a[href$='.pdf']": ["first-link"],
            "article + article": ["second"],
            "article ~ aside": ["aside"],
            "article:first-child": ["first"],
            "article:nth-child(2)": ["second"],
            "aside:last-child": ["aside"],
            "p:only-child": ["empty"],
            "p:empty": ["empty"],
            "*.featured": ["first"],
            "[data-locale='en-US']": ["first"],
            "[data-locale^='en']": ["first"],
            "[data-locale*='-U']": ["first"],
            "[data-locale|='en']": ["first"],
            "[class~='featured']": ["first"],
            "article, aside": ["first", "second", "aside"],
        }
        for selector, expected_ids in cases.items():
            with self.subTest(selector=selector):
                self.assertEqual(
                    [element_id for _, element_id in self._matches(selector)],
                    expected_ids,
                )

    def test_optional_document_scope_and_first_match_per_document(self) -> None:
        self.assertEqual(
            self._matches("#first", document_id=None),
            [("doc-one", "first"), ("doc-two", "first")],
        )
        self.assertEqual(
            self.catalogue.connection.execute(
                """
                SELECT
                    document_id,
                    atlas.macros.get_attribute(attributes, 'id') AS id
                FROM atlas.macros.query_selector('a')
                ORDER BY document_id
                """
            ).fetchall(),
            [("doc-one", "first-link"), ("doc-two", "first-link")],
        )
        self.assertEqual(
            self.catalogue.connection.execute(
                """
                SELECT atlas.macros.get_attribute(attributes, 'id')
                FROM atlas.macros.query_selector('a', 'doc-two')
                """
            ).fetchall(),
            [("first-link",)],
        )

    def test_rejects_unsupported_and_unbalanced_selectors(self) -> None:
        for selector in ("", "article:not(.featured)", "article]", "article >"):
            with self.subTest(selector=selector):
                with self.assertRaises(duckdb.Error):
                    self._matches(selector)


if __name__ == "__main__":
    unittest.main()
