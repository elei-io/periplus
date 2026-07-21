from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ducklake_client import DiskStorage, DuckDBCatalog
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from control.catalogue_fixtures import (
    seed_catalogue_fixtures,
    seed_system_catalogue_fixtures,
)
from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_queries.models import CatalogueQuery, CatalogueQueryRevision
from control.catalogue_scalar_macros.models import CatalogueScalarMacroDefinition
from control.catalogue_table_macros.models import CatalogueTableMacroDefinition
from control.catalogue_table_macros.service import create_definition
from control.urls import normalize_url as normalize_runtime_url
from control.catalogue_views.models import CatalogueViewReference
from db import Base
from dom import links_from_html
from repository import (
    FileObjectStore,
    RawHtmlRepository,
    RepositoryIngestor,
)
from repository.catalogue import Catalogue, CatalogueConfig, CrawlRecord
from repository.catalogue.materializations import MaterializationStore
from repository.catalogue.table_macros import CatalogueTableMacroStore
from repository.catalogue.views import CatalogueViewConflictError
from tests.catalogue_helpers import crawl_url_evidence
from repository.catalogue.views import CatalogueViewStore


class CatalogueFixtureTests(unittest.TestCase):
    def test_system_macros_are_seeded_explicitly_from_fixture_files(self) -> None:
        fixtures = Path(__file__).parents[2] / "fixtures"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            engine = create_engine("sqlite://")
            Base.metadata.create_all(
                engine, tables=[CatalogueScalarMacroDefinition.__table__]
            )
            session = Session(engine)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                before = catalogue.connection.execute(
                    """
                    SELECT function_name
                    FROM duckdb_functions()
                    WHERE database_name = 'atlas' AND schema_name = 'macros'
                      AND function_type = 'macro'
                    ORDER BY function_name
                    """
                ).fetchall()
                seed_system_catalogue_fixtures(session, catalogue, fixtures)
                after = catalogue.connection.execute(
                    """
                    SELECT function_name
                    FROM duckdb_functions()
                    WHERE database_name = 'atlas' AND schema_name = 'macros'
                      AND function_type = 'macro'
                    ORDER BY function_name
                    """
                ).fetchall()
                valid_urls = [
                    " HTTPS://Example.COM:443/a?utm_source=x&b=2&a=hello%20world#fragment ",
                    "http://[2001:db8::1]:80/x?q=a+b",
                    "https://example.com/path?empty=&bare",
                ]
                normalized_urls = [
                    catalogue.connection.execute(
                        "SELECT atlas.macros.normalize_url(?)", [value]
                    ).fetchone()[0]
                    for value in valid_urls
                ]
                invalid_urls = [
                    "mailto:a@example.com",
                    "https://user:pass@example.com/x",
                    "https://example.com:not-a-port/x",
                    "https://example.com:999999999999999999999/x",
                ]
                rejected_urls = [
                    catalogue.connection.execute(
                        "SELECT atlas.macros.normalize_url(?)", [value]
                    ).fetchone()[0]
                    for value in invalid_urls
                ]
                malformed_query_urls = [
                    catalogue.connection.execute(
                        "SELECT atlas.macros.normalize_url(?)", [value]
                    ).fetchone()[0]
                    for value in [
                        "https://example.com/path?q=Rond%F3",
                        "https://example.com/path?Rond%F3=value&ok=yes",
                    ]
                ]
                absent_optional_parts = catalogue.connection.execute(
                    """
                    SELECT
                        (atlas.macros.url_parts(?)).query,
                        (atlas.macros.url_parts(?)).fragment
                    """,
                    ["https://example.com/path", "https://example.com/path"],
                ).fetchone()
            session.close()
            engine.dispose()

        self.assertEqual(before, [])
        self.assertEqual(
            [row[0] for row in after],
            [
                "get_attribute",
                "has_attribute",
                "has_text",
                "inner_html",
                "normalize_url",
                "readable_text",
                "resolve_url",
                "text_content",
                "url_parts",
            ],
        )
        self.assertEqual(
            normalized_urls,
            [normalize_runtime_url(value) for value in valid_urls],
        )
        self.assertEqual(rejected_urls, [None] * len(invalid_urls))
        self.assertEqual(
            malformed_query_urls,
            [
                "https://example.com/path",
                "https://example.com/path?ok=yes",
            ],
        )
        self.assertEqual(absent_optional_parts, (None, None))

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
                    CatalogueScalarMacroDefinition.__table__,
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
                    first_page_links = session.scalar(
                        select(CatalogueMaterialization).where(
                            CatalogueMaterialization.name == "page_links"
                        )
                    )
                    self.assertIsNotNone(first_page_links)
                    assert first_page_links is not None
                    first_incarnation_id = first_page_links.id
                    json_ld_nodes = session.scalar(
                        select(CatalogueViewReference).where(
                            CatalogueViewReference.fixture_path
                            == "views/json_ld_nodes.sql"
                        )
                    )
                    self.assertIsNotNone(json_ld_nodes)
                    assert json_ld_nodes is not None
                    missing_uuid = json_ld_nodes.ducklake_view_uuid
                    CatalogueViewStore(catalogue).drop(
                        current_uuid=missing_uuid
                    )
                    seed_catalogue_fixtures(session, catalogue, fixtures)
                    session.refresh(json_ld_nodes)
                    self.assertNotEqual(
                        json_ld_nodes.ducklake_view_uuid,
                        missing_uuid,
                    )
                    self.assertIsNotNone(
                        CatalogueViewStore(catalogue).get(
                            json_ld_nodes.ducklake_view_uuid
                        )
                    )
                    self.assertEqual(
                        [
                            macro.macro_name
                            for macro in CatalogueTableMacroStore(catalogue).list()
                        ],
                        [
                            "extract_json_ld",
                            "extract_records",
                            "query_selector",
                            "query_selector_all",
                            "suggest_json_ld_schemas",
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
                    page_links = session.scalar(
                        select(CatalogueMaterialization).where(
                            CatalogueMaterialization.name == "page_links"
                        )
                    )
                    self.assertIsNotNone(page_links)
                    assert page_links is not None
                    self.assertEqual(page_links.id, first_incarnation_id)
                    self.assertEqual(
                        len(
                            list(
                                session.scalars(
                                    select(CatalogueMaterialization).where(
                                        CatalogueMaterialization.archived_at.is_(None)
                                    )
                                )
                            )
                        ),
                        3,
                    )
                    self.assertEqual(page_links.source_table, "crawls")
                    self.assertEqual(page_links.refresh_strategy, "keyed")
                    self.assertEqual(page_links.key_columns, ["crawl_id"])
                    self.assertEqual(page_links.partition_column, "captured_at")
                    self.assertEqual(page_links.observed_state, "creating")
                    self.assertIsNone(page_links.ducklake_table_uuid)
                    self.assertEqual(page_links.desired_state, "live")
                    reference = session.get(
                        CatalogueViewReference, page_links.view_reference_id
                    )
                    self.assertIsNotNone(reference)
                    assert reference is not None
                    self.assertEqual(
                        reference.fixture_path,
                        "materialized_views/crawl/page_links.sql",
                    )
                    selector_definitions = list(
                        session.scalars(
                            select(CatalogueTableMacroDefinition).where(
                                CatalogueTableMacroDefinition.macro_name.in_(
                                    ("query_selector", "query_selector_all")
                                )
                            )
                        )
                    )
                    self.assertEqual(len(selector_definitions), 2)
                    self.assertTrue(
                        all(
                            definition.parameter_defaults == {"document_id": "''"}
                            for definition in selector_definitions
                        )
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
                    CatalogueScalarMacroDefinition.__table__,
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
                        crawl_id = uuid4()
                        requested_url, urls, attempts = crawl_url_evidence(
                            crawl_id,
                            f"{url}/{index}",
                            captured_at=datetime(2026, 7, 18, tzinfo=UTC),
                            final_url=f"{url}/{index}",
                        )
                        crawl = CrawlRecord(
                            crawl_id=crawl_id,
                            document_id=captured_document_id,
                            graph_id=uuid4(),
                            graph_run_id=uuid4(),
                            graph_node_id=uuid4(),
                            crawl_request_id=uuid4(),
                            requested_url_id=requested_url.url_id,
                            final_url_id=requested_url.url_id,
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
                                    urls=urls,
                                    crawl_attempts=attempts,
                                )
                            ]
                        )

                    page_metadata = session.scalar(
                        select(CatalogueMaterialization).where(
                            CatalogueMaterialization.name == "page_metadata"
                        )
                    )
                    assert page_metadata is not None
                    row = catalogue.connection.execute(
                        f"""
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
                        FROM ({page_metadata.source_sql}) AS page_metadata
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
                        f"""
                        SELECT description
                        FROM ({page_metadata.source_sql}) AS page_metadata
                        WHERE document_id = ?
                        """,
                        [blank_document_id],
                    ).fetchone()
                    self.assertEqual(blank_description, (None,))
                    ingestor.close()
            finally:
                session.close()
            engine.dispose()

    def test_materialized_fixture_schema_change_recreates_its_backing_table(
        self,
    ) -> None:
        source_fixtures = Path(__file__).parents[2] / "fixtures"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixtures = root / "fixtures"
            shutil.copytree(source_fixtures, fixtures)
            engine = create_engine("sqlite://")
            Base.metadata.create_all(
                engine,
                tables=[
                    CatalogueQuery.__table__,
                    CatalogueQueryRevision.__table__,
                    CatalogueViewReference.__table__,
                    CatalogueMaterialization.__table__,
                    CatalogueTableMacroDefinition.__table__,
                    CatalogueScalarMacroDefinition.__table__,
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
                    original = session.scalar(
                        select(CatalogueMaterialization).where(
                            CatalogueMaterialization.name == "page_links",
                            CatalogueMaterialization.archived_at.is_(None),
                        )
                    )
                    self.assertIsNotNone(original)
                    assert original is not None
                    original_id = original.id
                    original_table_uuid = original.ducklake_table_uuid

                    (
                        fixtures
                        / "materialized_views"
                        / "crawl"
                        / "page_links.sql"
                    ).write_text(
                        "-- atlas:refresh=keyed(crawl_id)\n"
                        "CREATE VIEW views.page_links AS "
                        "SELECT crawl_id FROM crawls;",
                        encoding="utf-8",
                    )
                    with self.assertRaises(CatalogueViewConflictError):
                        seed_catalogue_fixtures(session, catalogue, fixtures)
                    self.assertEqual(original.id, original_id)
                    self.assertEqual(
                        original.ducklake_table_uuid, original_table_uuid
                    )
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
                    CatalogueScalarMacroDefinition.__table__,
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
                    crawl_id = uuid4()
                    requested_url, urls, attempts = crawl_url_evidence(
                        crawl_id,
                        url,
                        captured_at=datetime(2026, 7, 18, tzinfo=UTC),
                        final_url=url,
                    )
                    crawl = CrawlRecord(
                        crawl_id=crawl_id,
                        document_id=document_id,
                        graph_id=uuid4(),
                        graph_run_id=uuid4(),
                        graph_node_id=uuid4(),
                        crawl_request_id=uuid4(),
                        requested_url_id=requested_url.url_id,
                        final_url_id=requested_url.url_id,
                        captured_at=datetime(2026, 7, 18, tzinfo=UTC),
                        status_code=200,
                        duration_ms=1,
                        policy_config_hash="a" * 64,
                        policy_config_json={},
                        outcome="success",
                    )
                    ingestor.commit_prepared_batch(
                        [ingestor.prepare_from_raw(
                            crawl=crawl, urls=urls, crawl_attempts=attempts
                        )]
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

                    suggestions = catalogue.connection.execute(
                        """
                        SELECT entity_type, matched_node_count
                        FROM atlas.macros.suggest_json_ld_schemas(?)
                        """,
                        [url],
                    ).fetchall()
                    self.assertEqual(
                        suggestions,
                        [
                            ("Product", 2),
                            ("Thing", 2),
                            ("Offer", 1),
                            ("Organization", 1),
                            ("WebSite", 1),
                        ],
                    )

                    suggestion = catalogue.connection.execute(
                        """
                        SELECT
                            entity_type,
                            matched_crawl_count,
                            matched_document_count,
                            matched_node_count,
                            first_captured_at,
                            last_captured_at,
                            inferred_schema,
                            example_node,
                            extract_sql
                        FROM atlas.macros.suggest_json_ld_schemas(?)
                        WHERE entity_type = 'Product'
                        """,
                        [url],
                    ).fetchone()
                    self.assertIsNotNone(suggestion)
                    assert suggestion is not None
                    self.assertEqual(suggestion[:4], ("Product", 1, 1, 2))
                    self.assertEqual(
                        suggestion[4:6],
                        (
                            datetime(2026, 7, 18, tzinfo=UTC),
                            datetime(2026, 7, 18, tzinfo=UTC),
                        ),
                    )
                    self.assertIn('"name":"VARCHAR"', suggestion[6])
                    self.assertIn('"offers":', suggestion[6])
                    self.assertIn('"Widget"', str(suggestion[7]))
                    self.assertIn(
                        "FROM macros.extract_json_ld(",
                        suggestion[8],
                    )

                    extracted_cursor = catalogue.connection.execute(suggestion[8])
                    extracted_columns = [
                        str(column[0]) for column in extracted_cursor.description
                    ]
                    extracted = extracted_cursor.fetchall()
                    self.assertEqual(len(extracted), 2)
                    self.assertNotIn("entity", extracted_columns)
                    self.assertIn("crawl_id", extracted_columns)
                    self.assertIn("page_url", extracted_columns)
                    self.assertIn("node_json", extracted_columns)
                    name_index = extracted_columns.index("name")
                    offers_index = extracted_columns.index("offers")
                    by_name = {row[name_index]: row for row in extracted}
                    self.assertEqual(set(by_name), {"Widget", "Array widget"})
                    self.assertEqual(
                        by_name["Widget"][offers_index]["price"],
                        "12.50",
                    )
                    self.assertIsNone(by_name["Array widget"][offers_index])
                    ingestor.close()
            finally:
                session.close()
                engine.dispose()

    def test_drops_retired_fixture_owned_macros_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixtures = root / "fixtures"
            for kind in ("queries", "views", "table_macros"):
                (fixtures / kind).mkdir(parents=True)
            shutil.copytree(
                Path(__file__).parents[2] / "fixtures" / "scalar_macros",
                fixtures / "scalar_macros",
            )
            fixture = fixtures / "table_macros" / "temporary.sql"
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
                    CatalogueScalarMacroDefinition.__table__,
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
                    seed_system_catalogue_fixtures(
                        session, catalogue, Path(__file__).parents[2] / "fixtures"
                    )
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
        <a href="#details">Details</a>
        <a href="?page=2&amp;utm_source=mail&amp;b=2&amp;a=1">Next</a>
        <a href="https://blog.example.com/post">Blog</a>
        <a href="http://example.com/other">HTTP</a>
        <a href="//outside.test/x#section">Outside</a>
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
                    CatalogueScalarMacroDefinition.__table__,
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
                    crawl_id = uuid4()
                    requested_url, urls, attempts = crawl_url_evidence(
                        crawl_id,
                        url,
                        captured_at=datetime(2026, 7, 17, tzinfo=UTC),
                        final_url=url,
                    )
                    crawl = CrawlRecord(
                        crawl_id=crawl_id,
                        document_id=f"sha256:{hashlib.sha256(html.encode()).hexdigest()}",
                        graph_id=uuid4(),
                        graph_run_id=uuid4(),
                        graph_node_id=uuid4(),
                        crawl_request_id=uuid4(),
                        requested_url_id=requested_url.url_id,
                        final_url_id=requested_url.url_id,
                        captured_at=datetime(2026, 7, 17, tzinfo=UTC),
                        status_code=200,
                        duration_ms=1,
                        policy_config_hash="a" * 64,
                        policy_config_json={},
                        outcome="success",
                    )
                    ingestor.commit_prepared_batch(
                        [ingestor.prepare_from_raw(
                            crawl=crawl, urls=urls, crawl_attempts=attempts
                        )]
                    )

                    self.assertGreater(
                        len(
                            catalogue.connection.execute(
                                "SELECT * FROM atlas.views.page_links "
                                "WHERE crawl_id = ?",
                                [crawl.crawl_id],
                            ).fetchall()
                        ),
                        0,
                    )
                    materialization = session.scalar(
                        select(CatalogueMaterialization).where(
                            CatalogueMaterialization.name == "page_links"
                        )
                    )
                    self.assertIsNotNone(materialization)
                    assert materialization is not None
                    page_links = catalogue.connection.execute(
                        f"""
                        WITH links AS (
                            SELECT *
                            FROM ({materialization.source_sql}) AS page_links_source
                            WHERE crawl_id = ?
                        )
                        SELECT
                            target.normalized_url,
                            source.host,
                            target.path,
                            links.relation_kind
                        FROM links
                        JOIN urls AS source ON source.url_id = links.source_url_id
                        JOIN urls AS target ON target.url_id = links.target_url_id
                        ORDER BY element_index
                        LIMIT 2
                        """,
                        [crawl.crawl_id],
                    ).fetchall()
                    self.assertEqual(
                        page_links,
                        [
                            (
                                "https://example.com/one",
                                "example.com",
                                "/one",
                                "same_origin",
                            ),
                            (
                                "https://example.com/two",
                                "example.com",
                                "/two",
                                "same_origin",
                            ),
                        ],
                    )
                    durable_rows = catalogue.connection.execute(
                        f"""
                        SELECT *
                        FROM ({materialization.source_sql}) AS page_links_source
                        WHERE crawl_id = ?
                        ORDER BY element_index
                        """,
                        [crawl.crawl_id],
                    ).fetchall()
                    projected = links_from_html(html, page_url=url)
                    expected_rows = []
                    for link in sorted(
                        projected["internal"] + projected["external"],
                        key=lambda item: int(item["element_index"]),
                    ):
                        expected_rows.append(
                            (
                                crawl.crawl_id,
                                crawl.document_id,
                                crawl.captured_at,
                                link["element_index"],
                                requested_url.url_id,
                                hashlib.sha256(
                                    str(link["target_url"]).encode()
                                ).hexdigest(),
                                link["raw_href"],
                                link["target_fragment"],
                                link["relation_kind"],
                            )
                        )
                    self.assertEqual(durable_rows, expected_rows)

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
                    CatalogueScalarMacroDefinition.__table__,
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
                        crawl_id = uuid4()
                        requested_url, urls, attempts = crawl_url_evidence(
                            crawl_id,
                            url,
                            captured_at=captured_at,
                            final_url=url,
                        )
                        crawl = CrawlRecord(
                            crawl_id=crawl_id,
                            document_id=(
                                "sha256:"
                                f"{hashlib.sha256(html.encode()).hexdigest()}"
                            ),
                            graph_id=uuid4(),
                            graph_run_id=uuid4(),
                            graph_node_id=uuid4(),
                            crawl_request_id=uuid4(),
                            requested_url_id=requested_url.url_id,
                            final_url_id=requested_url.url_id,
                            captured_at=captured_at,
                            status_code=200,
                            duration_ms=1,
                            policy_config_hash="a" * 64,
                            policy_config_json={},
                            outcome="success",
                        )
                        ingestor.commit_prepared_batch(
                            [ingestor.prepare_from_raw(
                                crawl=crawl, urls=urls, crawl_attempts=attempts
                            )]
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
            for kind in ("queries", "views", "table_macros"):
                (fixtures / kind).mkdir(parents=True)
            shutil.copytree(
                Path(__file__).parents[2] / "fixtures" / "scalar_macros",
                fixtures / "scalar_macros",
            )
            (fixtures / "queries" / "recent_documents.sql").write_text(
                "SELECT document_id FROM documents LIMIT 10;", encoding="utf-8"
            )
            (fixtures / "views" / "document_ids.sql").write_text(
                "CREATE VIEW views.document_ids AS "
                "SELECT document_id FROM documents;",
                encoding="utf-8",
            )
            (fixtures / "table_macros" / "numbers_from.sql").write_text(
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
                    CatalogueScalarMacroDefinition.__table__,
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
                    seed_system_catalogue_fixtures(
                        session, catalogue, Path(__file__).parents[2] / "fixtures"
                    )
                    seed_catalogue_fixtures(session, catalogue, fixtures)
                    query = session.scalar(select(CatalogueQuery))
                    view = session.scalar(select(CatalogueViewReference))
                    macro = session.scalar(select(CatalogueTableMacroDefinition))
                    self.assertEqual(query.fixture_path, "queries/recent_documents.sql")
                    self.assertEqual(view.fixture_path, "views/document_ids.sql")
                    self.assertEqual(macro.fixture_path, "table_macros/numbers_from.sql")
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
