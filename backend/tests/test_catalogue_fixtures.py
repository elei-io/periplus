from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ducklake_client import DiskStorage, DuckDBCatalog
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from control.catalogue_fixtures import seed_catalogue_fixtures
from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_queries.models import CatalogueQuery, CatalogueQueryRevision
from control.catalogue_table_macros.models import CatalogueTableMacroDefinition
from control.catalogue_table_macros.service import create_definition
from control.catalogue_views.models import CatalogueViewReference
from db import Base
from repository import (
    FileObjectStore,
    RawHtmlRepository,
    RepositoryIngestor,
)
from repository.catalogue import Catalogue, CatalogueConfig, CrawlRecord
from repository.catalogue.table_macros import CatalogueTableMacroStore
from repository.catalogue.views import CatalogueViewStore


class CatalogueFixtureTests(unittest.TestCase):
    def test_bundled_fixtures_compile_against_the_catalogue(self) -> None:
        fixtures = Path(__file__).parents[2] / "fixtures"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            engine = create_engine("sqlite://")
            Base.metadata.create_all(
                engine,
                tables=[
                    CatalogueQuery.__table__,
                    CatalogueQueryRevision.__table__,
                    CatalogueViewReference.__table__,
                    CatalogueMaterialization.__table__,
                    CatalogueTableMacroDefinition.__table__,
                ],
            )
            session = Session(engine, expire_on_commit=False)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            try:
                with Catalogue(config) as catalogue:
                    catalogue.bootstrap()
                    seed_catalogue_fixtures(session, catalogue, fixtures)
                    self.assertEqual(
                        [
                            macro.macro_name
                            for macro in CatalogueTableMacroStore(catalogue).list()
                        ],
                        [
                            "extract_records",
                            "suggest_records",
                        ],
                    )
                    self.assertEqual(
                        catalogue.connection.execute(
                            "SELECT * FROM atlas.macros.suggest_records('missing')"
                        ).fetchall(),
                        [],
                    )
                    self.assertEqual(
                        catalogue.connection.execute(
                            "SELECT * FROM atlas.macros.extract_records('missing', 'x > y')"
                        ).fetchall(),
                        [],
                    )
            finally:
                session.close()
                engine.dispose()

    def test_page_metadata_fixture_extracts_document_scoped_metadata(self) -> None:
        url = "https://example.com/catalogue/widget"
        html = """
        <!doctype html>
        <html lang="en-GB">
          <head>
            <base href="
              /catalogue/
            ">
            <title>
              Example   Widget
            </title>
            <meta name="Description" content="
              A useful   widget.
            ">
            <meta name="robots" content="index,follow">
            <link rel="alternate CANONICAL" href="
              ../widget
            ">
            <meta property="OG:TITLE" content="Widget preview">
            <meta property="og:description" content="
              Preview   description
            ">
            <meta property="og:image" content="
              /images/widget.png
            ">
          </head>
          <body><h1>Example Widget</h1></body>
        </html>
        """
        blank_html = """
        <!doctype html>
        <html>
          <head>
            <title>Metadata-free page</title>
            <meta name="description" content="
            ">
          </head>
          <body></body>
        </html>
        """
        document_id = f"sha256:{hashlib.sha256(html.encode()).hexdigest()}"
        blank_document_id = (
            f"sha256:{hashlib.sha256(blank_html.encode()).hexdigest()}"
        )
        fixtures = Path(__file__).parents[2] / "fixtures"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            engine = create_engine("sqlite://")
            Base.metadata.create_all(
                engine,
                tables=[
                    CatalogueQuery.__table__,
                    CatalogueQueryRevision.__table__,
                    CatalogueViewReference.__table__,
                    CatalogueMaterialization.__table__,
                    CatalogueTableMacroDefinition.__table__,
                ],
            )
            session = Session(engine, expire_on_commit=False)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            try:
                with Catalogue(config) as catalogue:
                    catalogue.bootstrap()
                    seed_catalogue_fixtures(session, catalogue, fixtures)
                    ingestor = RepositoryIngestor(
                        html_repository=RawHtmlRepository(
                            FileObjectStore(root / "objects")
                        ),
                        catalogue=catalogue,
                        staging_root=root / "staging",
                    )
                    for index, (captured_html, captured_document_id) in enumerate(
                        (
                            (html, document_id),
                            (blank_html, blank_document_id),
                        )
                    ):
                        ingestor.store_raw(captured_html)
                        crawl = CrawlRecord(
                            crawl_id=uuid4(),
                            document_id=captured_document_id,
                            graph_id=uuid4(),
                            graph_run_id=uuid4(),
                            graph_node_id=uuid4(),
                            crawl_request_id=uuid4(),
                            requested_url=f"{url}/{index}",
                            normalized_url=f"{url}/{index}",
                            final_url=f"{url}/{index}",
                            captured_at=datetime(2026, 7, 18, tzinfo=UTC),
                            status_code=200,
                            duration_ms=1,
                            policy_config_hash="a" * 64,
                            policy_config_json={},
                            outcome="success",
                        )
                        ingestor.commit_prepared_batch(
                            [
                                ingestor.prepare_from_raw(
                                    crawl=crawl,
                                )
                            ]
                        )

                    row = catalogue.connection.execute(
                        """
                        SELECT
                            document_id,
                            language,
                            title,
                            description,
                            canonical_href,
                            base_href,
                            robots,
                            open_graph_title,
                            open_graph_description,
                            open_graph_image
                        FROM atlas.views.page_metadata
                        WHERE document_id = ?
                        """,
                        [document_id],
                    ).fetchone()

                    self.assertEqual(
                        row,
                        (
                            document_id,
                            "en-GB",
                            "Example Widget",
                            "A useful widget.",
                            "../widget",
                            "/catalogue/",
                            "index,follow",
                            "Widget preview",
                            "Preview description",
                            "/images/widget.png",
                        ),
                    )
                    blank_description = catalogue.connection.execute(
                        """
                        SELECT description
                        FROM atlas.views.page_metadata
                        WHERE document_id = ?
                        """,
                        [blank_document_id],
                    ).fetchone()
                    self.assertEqual(blank_description, (None,))
                    ingestor.close()
            finally:
                session.close()
                engine.dispose()

    def test_json_ld_fixtures_preserve_scripts_and_expand_top_level_nodes(
        self,
    ) -> None:
        url = "https://example.com/products/widget"
        html = """
        <!doctype html>
        <html>
          <head>
            <script type=" Application/LD+JSON ">
              {
                "@context": "https://schema.org",
                "@id": "https://example.com/products/widget",
                "@type": "Product",
                "name": "Widget",
                "offers": {"@type": "Offer", "price": "12.50"}
              }
            </script>
            <script type="application/ld+json">
              [
                {"@type": "Product", "name": "Array widget"},
                17,
                {
                  "@context": {"schema": "https://schema.org"},
                  "@type": ["Thing", "Offer"],
                  "name": "Bundle"
                }
              ]
            </script>
            <script type="application/ld+json">
              {
                "@context": "https://schema.org",
                "@graph": [
                  {"@id": "#site", "@type": "WebSite"},
                  {"@id": "#org", "@type": ["Organization", "Thing"]}
                ]
              }
            </script>
            <script type="application/ld+json">{not valid JSON}</script>
            <script type="application/ld+json">
            </script>
          </head>
          <body></body>
        </html>
        """
        document_id = f"sha256:{hashlib.sha256(html.encode()).hexdigest()}"
        fixtures = Path(__file__).parents[2] / "fixtures"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            engine = create_engine("sqlite://")
            Base.metadata.create_all(
                engine,
                tables=[
                    CatalogueQuery.__table__,
                    CatalogueQueryRevision.__table__,
                    CatalogueViewReference.__table__,
                    CatalogueMaterialization.__table__,
                    CatalogueTableMacroDefinition.__table__,
                ],
            )
            session = Session(engine, expire_on_commit=False)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            try:
                with Catalogue(config) as catalogue:
                    catalogue.bootstrap()
                    seed_catalogue_fixtures(session, catalogue, fixtures)
                    ingestor = RepositoryIngestor(
                        html_repository=RawHtmlRepository(
                            FileObjectStore(root / "objects")
                        ),
                        catalogue=catalogue,
                        staging_root=root / "staging",
                    )
                    ingestor.store_raw(html)
                    crawl = CrawlRecord(
                        crawl_id=uuid4(),
                        document_id=document_id,
                        graph_id=uuid4(),
                        graph_run_id=uuid4(),
                        graph_node_id=uuid4(),
                        crawl_request_id=uuid4(),
                        requested_url=url,
                        normalized_url=url,
                        final_url=url,
                        captured_at=datetime(2026, 7, 18, tzinfo=UTC),
                        status_code=200,
                        duration_ms=1,
                        policy_config_hash="a" * 64,
                        policy_config_json={},
                        outcome="success",
                    )
                    ingestor.commit_prepared_batch(
                        [ingestor.prepare_from_raw(crawl=crawl)]
                    )

                    scripts = catalogue.connection.execute(
                        """
                        SELECT script_ordinal, is_valid, root_type
                        FROM atlas.views.json_ld_scripts
                        WHERE document_id = ?
                        ORDER BY script_ordinal
                        """,
                        [document_id],
                    ).fetchall()
                    self.assertEqual(
                        scripts,
                        [
                            (1, True, "OBJECT"),
                            (2, True, "ARRAY"),
                            (3, True, "OBJECT"),
                            (4, False, None),
                            (5, False, None),
                        ],
                    )

                    nodes = catalogue.connection.execute(
                        """
                        SELECT
                            script_ordinal,
                            node_ordinal,
                            json_path,
                            json_extract_string(context_json, '$') AS context,
                            node_id,
                            node_types,
                            json_extract_string(node_json, '$.name') AS name,
                            json_extract_string(
                                node_json,
                                '$.offers.price'
                            ) AS price
                        FROM atlas.views.json_ld_nodes
                        WHERE document_id = ?
                        ORDER BY script_ordinal, node_ordinal
                        """,
                        [document_id],
                    ).fetchall()
                    self.assertEqual(
                        nodes,
                        [
                            (
                                1,
                                1,
                                "$",
                                "https://schema.org",
                                "https://example.com/products/widget",
                                ["Product"],
                                "Widget",
                                "12.50",
                            ),
                            (
                                2,
                                1,
                                "$[0]",
                                None,
                                None,
                                ["Product"],
                                "Array widget",
                                None,
                            ),
                            (
                                2,
                                3,
                                "$[2]",
                                '{"schema":"https://schema.org"}',
                                None,
                                ["Thing", "Offer"],
                                "Bundle",
                                None,
                            ),
                            (
                                3,
                                1,
                                '$."@graph"[0]',
                                "https://schema.org",
                                "#site",
                                ["WebSite"],
                                None,
                                None,
                            ),
                            (
                                3,
                                2,
                                '$."@graph"[1]',
                                "https://schema.org",
                                "#org",
                                ["Organization", "Thing"],
                                None,
                                None,
                            ),
                        ],
                    )
                    ingestor.close()
            finally:
                session.close()
                engine.dispose()

    def test_drops_retired_fixture_owned_macros_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixtures = root / "fixtures"
            for kind in ("queries", "views", "macros"):
                (fixtures / kind).mkdir(parents=True)
            fixture = fixtures / "macros" / "temporary.sql"
            fixture.write_text(
                "CREATE MACRO macros.temporary() AS TABLE (SELECT 1 AS value);",
                encoding="utf-8",
            )

            engine = create_engine("sqlite://")
            Base.metadata.create_all(
                engine,
                tables=[
                    CatalogueQuery.__table__,
                    CatalogueQueryRevision.__table__,
                    CatalogueViewReference.__table__,
                    CatalogueMaterialization.__table__,
                    CatalogueTableMacroDefinition.__table__,
                ],
            )
            session = Session(engine, expire_on_commit=False)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            try:
                with Catalogue(config) as catalogue:
                    catalogue.bootstrap()
                    store = CatalogueTableMacroStore(catalogue)
                    seed_catalogue_fixtures(session, catalogue, fixtures)
                    create_definition(
                        session,
                        store,
                        slug="user_owned",
                        parameters=[],
                        sql="SELECT 2 AS value",
                        description=None,
                    )

                    fixture.unlink()
                    seed_catalogue_fixtures(session, catalogue, fixtures)

                    self.assertEqual(
                        [macro.macro_name for macro in store.list()],
                        ["user_owned"],
                    )
                    definitions = list(
                        session.scalars(select(CatalogueTableMacroDefinition))
                    )
                    self.assertEqual(len(definitions), 1)
                    self.assertEqual(definitions[0].macro_name, "user_owned")
                    self.assertIsNone(definitions[0].fixture_path)
            finally:
                session.close()
                engine.dispose()

    def test_record_macros_use_stable_anchors_and_semantic_class_tokens(
        self,
    ) -> None:
        url = "https://example.com/products"
        html = """
        <html><body><ul class="products layout-grid">
          <li class="product post-101"><a data-testid="item-tile" href="/one">
            <p class="star-rating One"></p><h3>Alpha</h3><span class="badge">£1.00</span>
          </a></li>
          <li class="product post-102"><a data-testid="item-tile" href="/two">
            <p class="star-rating Two"></p><h3>Beta</h3><span class="badge">£2.00</span>
          </a></li>
        </ul><ul class="products layout-grid">
          <li class="product post-103"><a data-testid="item-tile" href="/three">
            <p class="star-rating Three"></p><h3>Gamma</h3><span>£3.00</span>
          </a></li>
        </ul>
        <div class="cards">
          <a class="card" href="/member/a"><span>Ada</span></a>
          <a class="card" href="/member/b"><span>Bea</span></a>
          <a class="card" href="/member/c"><span>Cy</span></a>
        </div>
        <dl>
          <dt><a href="/paper/1">paper-1</a></dt><dd><h3>First paper</h3><p>Alice</p></dd>
          <dt><a href="/paper/2">paper-2</a></dt><dd><h3>Second paper</h3><p>Bob</p></dd>
          <dt><a href="/paper/3">paper-3</a></dt><dd><h3>Third paper</h3><p>Carol</p></dd>
        </dl>
        <ol><li>First list A</li><li>First list B</li></ol>
        <ol><li>Second list A</li><li>Second list B</li></ol>
        </body></html>
        """
        fixtures = Path(__file__).parents[2] / "fixtures"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            engine = create_engine("sqlite://")
            Base.metadata.create_all(
                engine,
                tables=[
                    CatalogueQuery.__table__,
                    CatalogueQueryRevision.__table__,
                    CatalogueViewReference.__table__,
                    CatalogueMaterialization.__table__,
                    CatalogueTableMacroDefinition.__table__,
                ],
            )
            session = Session(engine, expire_on_commit=False)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            try:
                with Catalogue(config) as catalogue:
                    catalogue.bootstrap()
                    seed_catalogue_fixtures(session, catalogue, fixtures)
                    ingestor = RepositoryIngestor(
                        html_repository=RawHtmlRepository(
                            FileObjectStore(root / "objects")
                        ),
                        catalogue=catalogue,
                        staging_root=root / "staging",
                    )
                    ingestor.store_raw(html)
                    crawl = CrawlRecord(
                        crawl_id=uuid4(),
                        document_id=f"sha256:{hashlib.sha256(html.encode()).hexdigest()}",
                        graph_id=uuid4(),
                        graph_run_id=uuid4(),
                        graph_node_id=uuid4(),
                        crawl_request_id=uuid4(),
                        requested_url=url,
                        normalized_url=url,
                        final_url=url,
                        captured_at=datetime(2026, 7, 17, tzinfo=UTC),
                        status_code=200,
                        duration_ms=1,
                        policy_config_hash="a" * 64,
                        policy_config_json={},
                        outcome="success",
                    )
                    ingestor.commit_prepared_batch(
                        [ingestor.prepare_from_raw(crawl=crawl)]
                    )

                    suggestion = catalogue.connection.execute(
                        "SELECT record_selector, fields, matched_record_count "
                        "FROM atlas.macros.suggest_records(?) "
                        "WHERE record_selector = "
                        "'ul.products > li.product'",
                        [url],
                    ).fetchone()
                    self.assertIsNotNone(suggestion)
                    assert suggestion is not None
                    self.assertEqual(
                        suggestion[0],
                        "ul.products > li.product",
                    )
                    self.assertEqual(suggestion[2], 3)
                    sources = [field["source"] for field in suggestion[1]]
                    self.assertIn("derived:class_token", sources)
                    self.assertNotIn("attribute:class", sources)
                    self.assertNotIn(
                        "badge",
                        [
                            example
                            for field in suggestion[1]
                            if field["source"] == "derived:class_token"
                            for example in field["examples"]
                        ],
                    )

                    extracted = catalogue.connection.execute(
                        "SELECT field_definitions, field_1, field_2, field_3, "
                        "field_4, field_5, field_6, field_7, field_8, field_9, "
                        "field_10, field_11, field_12 "
                        "FROM atlas.macros.extract_records(?, ?) "
                        "ORDER BY record_number",
                        [url, suggestion[0]],
                    ).fetchall()
                    self.assertEqual(len(extracted), 3)
                    class_field = next(
                        index
                        for index, definition in enumerate(extracted[0][0], start=1)
                        if definition.endswith(" :: class_token")
                    )
                    self.assertEqual(
                        {row[class_field] for row in extracted},
                        {"One", "Two", "Three"},
                    )

                    member = catalogue.connection.execute(
                        "SELECT field_definitions, field_1, field_2, field_3, "
                        "field_4, field_5, field_6, field_7, field_8, field_9, "
                        "field_10, field_11, field_12 "
                        "FROM atlas.macros.extract_records("
                        "?, 'div.cards > a.card') ORDER BY record_number LIMIT 1",
                        [url],
                    ).fetchone()
                    self.assertIsNotNone(member)
                    assert member is not None
                    root_href_field = next(
                        index
                        for index, definition in enumerate(member[0], start=1)
                        if definition == ":scope :: resolved_href"
                    )
                    self.assertEqual(
                        member[root_href_field],
                        "https://example.com/member/a",
                    )

                    paper = catalogue.connection.execute(
                        "SELECT record_json FROM atlas.macros.extract_records("
                        "?, 'body > dl > dt') ORDER BY record_number LIMIT 1",
                        [url],
                    ).fetchone()
                    self.assertIsNotNone(paper)
                    assert paper is not None
                    self.assertIn("First paper", paper[0])
                    self.assertIn("Alice", paper[0])

                    scoped_lists = catalogue.connection.execute(
                        "SELECT record_selector, matched_record_count "
                        "FROM atlas.macros.suggest_records(?) "
                        "WHERE record_selector LIKE "
                        "'body > ol:nth-of-type(%) > li' "
                        "ORDER BY record_selector",
                        [url],
                    ).fetchall()
                    self.assertEqual(
                        scoped_lists,
                        [
                            ("body > ol:nth-of-type(1) > li", 2),
                            ("body > ol:nth-of-type(2) > li", 2),
                        ],
                    )
                    ingestor.close()
            finally:
                session.close()
                engine.dispose()

    def test_record_macros_match_url_patterns_across_crawl_history(self) -> None:
        first_url = "https://example.com/catalogue?page=1"
        second_url = "https://example.com/catalogue?page=2"
        first_capture = datetime(2026, 7, 15, tzinfo=UTC)
        second_capture = datetime(2026, 7, 16, tzinfo=UTC)
        third_capture = datetime(2026, 7, 17, tzinfo=UTC)
        observations = [
            (
                first_url,
                first_capture,
                "<html><body><ul class='items'>"
                "<li class='item'>A</li><li class='item'>B</li>"
                "</ul></body></html>",
            ),
            (
                first_url,
                second_capture,
                "<html><body><ul class='items'>"
                "<li class='item'>A2</li><li class='item'>B2</li>"
                "<li class='item'>C2</li></ul></body></html>",
            ),
            (
                second_url,
                third_capture,
                "<html><body><ul class='items'>"
                "<li class='item'>D</li><li class='item'>E</li>"
                "</ul></body></html>",
            ),
        ]
        fixtures = Path(__file__).parents[2] / "fixtures"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            engine = create_engine("sqlite://")
            Base.metadata.create_all(
                engine,
                tables=[
                    CatalogueQuery.__table__,
                    CatalogueQueryRevision.__table__,
                    CatalogueViewReference.__table__,
                    CatalogueMaterialization.__table__,
                    CatalogueTableMacroDefinition.__table__,
                ],
            )
            session = Session(engine, expire_on_commit=False)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            try:
                with Catalogue(config) as catalogue:
                    catalogue.bootstrap()
                    seed_catalogue_fixtures(session, catalogue, fixtures)
                    ingestor = RepositoryIngestor(
                        html_repository=RawHtmlRepository(
                            FileObjectStore(root / "objects")
                        ),
                        catalogue=catalogue,
                        staging_root=root / "staging",
                    )
                    for url, captured_at, html in observations:
                        ingestor.store_raw(html)
                        crawl = CrawlRecord(
                            crawl_id=uuid4(),
                            document_id=(
                                "sha256:"
                                f"{hashlib.sha256(html.encode()).hexdigest()}"
                            ),
                            graph_id=uuid4(),
                            graph_run_id=uuid4(),
                            graph_node_id=uuid4(),
                            crawl_request_id=uuid4(),
                            requested_url=url,
                            normalized_url=url,
                            final_url=url,
                            captured_at=captured_at,
                            status_code=200,
                            duration_ms=1,
                            policy_config_hash="a" * 64,
                            policy_config_json={},
                            outcome="success",
                        )
                        ingestor.commit_prepared_batch(
                            [ingestor.prepare_from_raw(crawl=crawl)]
                        )

                    selector = "ul.items > li.item"
                    exact = catalogue.connection.execute(
                        "SELECT crawl_id, captured_at, matched_record_count "
                        "FROM atlas.macros.extract_records(?, ?) "
                        "ORDER BY captured_at, record_number",
                        [first_url, selector],
                    ).fetchall()
                    self.assertEqual(len(exact), 5)
                    self.assertEqual(
                        {(row[1], row[2]) for row in exact},
                        {(first_capture, 2), (second_capture, 3)},
                    )

                    pattern = "HTTPS://EXAMPLE.COM/CATALOGUE?PAGE=%"
                    patterned = catalogue.connection.execute(
                        "SELECT crawl_id, page_url, captured_at, "
                        "matched_record_count, records_truncated "
                        "FROM atlas.macros.extract_records(?, ?) "
                        "ORDER BY captured_at, record_number",
                        [pattern, selector],
                    ).fetchall()
                    self.assertEqual(len(patterned), 7)
                    self.assertEqual(len({row[0] for row in patterned}), 3)
                    self.assertEqual(
                        {(row[1], row[2], row[3]) for row in patterned},
                        {
                            (first_url, first_capture, 2),
                            (first_url, second_capture, 3),
                            (second_url, third_capture, 2),
                        },
                    )
                    self.assertFalse(any(row[4] for row in patterned))

                    at_first_capture = catalogue.connection.execute(
                        "SELECT record_number "
                        "FROM atlas.macros.extract_records(?, ?) "
                        "WHERE captured_at = ?",
                        [pattern, selector, first_capture],
                    ).fetchall()
                    self.assertEqual(at_first_capture, [(1,), (2,)])

                    exact_case_mismatch = catalogue.connection.execute(
                        "SELECT * FROM atlas.macros.extract_records(?, ?)",
                        [first_url.upper(), selector],
                    ).fetchall()
                    self.assertEqual(exact_case_mismatch, [])

                    suggestion = catalogue.connection.execute(
                        "SELECT matched_crawl_count, matched_page_count, "
                        "first_captured_at, last_captured_at, "
                        "matched_record_count "
                        "FROM atlas.macros.suggest_records(?) "
                        "WHERE record_selector = ?",
                        [pattern, selector],
                    ).fetchone()
                    self.assertEqual(
                        suggestion,
                        (
                            3,
                            2,
                            first_capture,
                            third_capture,
                            7,
                        ),
                    )
                    ingestor.close()
            finally:
                session.close()
                engine.dispose()

    def test_seeds_all_definition_kinds_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixtures = root / "fixtures"
            for kind in ("queries", "views", "macros"):
                (fixtures / kind).mkdir(parents=True)
            (fixtures / "queries" / "recent_documents.sql").write_text(
                "SELECT document_id FROM documents LIMIT 10;", encoding="utf-8"
            )
            (fixtures / "views" / "document_ids.sql").write_text(
                "CREATE VIEW views.document_ids AS "
                "SELECT document_id FROM documents;",
                encoding="utf-8",
            )
            (fixtures / "macros" / "numbers_from.sql").write_text(
                "CREATE MACRO macros.numbers_from(p_minimum) AS TABLE ("
                "SELECT value FROM range(5) AS values(value) "
                "WHERE value >= p_minimum);",
                encoding="utf-8",
            )

            engine = create_engine("sqlite://")
            Base.metadata.create_all(
                engine,
                tables=[
                    CatalogueQuery.__table__,
                    CatalogueQueryRevision.__table__,
                    CatalogueViewReference.__table__,
                    CatalogueMaterialization.__table__,
                    CatalogueTableMacroDefinition.__table__,
                ],
            )
            session = Session(engine, expire_on_commit=False)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            try:
                with Catalogue(config) as catalogue:
                    catalogue.bootstrap()
                    seed_catalogue_fixtures(session, catalogue, fixtures)
                    query = session.scalar(select(CatalogueQuery))
                    view = session.scalar(select(CatalogueViewReference))
                    macro = session.scalar(select(CatalogueTableMacroDefinition))
                    self.assertEqual(query.fixture_path, "queries/recent_documents.sql")
                    self.assertEqual(view.fixture_path, "views/document_ids.sql")
                    self.assertEqual(macro.fixture_path, "macros/numbers_from.sql")
                    self.assertEqual(len(CatalogueViewStore(catalogue).list()), 1)
                    self.assertEqual(len(CatalogueTableMacroStore(catalogue).list()), 1)
                    self.assertEqual(
                        catalogue.connection.execute(
                            "SELECT * FROM atlas.macros.numbers_from(3) ORDER BY value"
                        ).fetchall(),
                        [(3,), (4,)],
                    )
                    identities = (
                        query.current_revision_id,
                        view.ducklake_view_uuid,
                        macro.definition_revision_id,
                    )

                    seed_catalogue_fixtures(session, catalogue, fixtures)
                    self.assertEqual(
                        (
                            query.current_revision_id,
                            view.ducklake_view_uuid,
                            macro.definition_revision_id,
                        ),
                        identities,
                    )
            finally:
                session.close()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
