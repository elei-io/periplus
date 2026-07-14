from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ducklake_client import DiskStorage, DuckDBCatalog
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from control.catalogue_fixtures import seed_catalogue_fixtures
from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_queries.models import CatalogueQuery, CatalogueQueryRevision
from control.catalogue_table_macros.models import CatalogueTableMacroDefinition
from control.catalogue_views.models import CatalogueViewReference
from db import Base
from repository.catalogue import Catalogue, CatalogueConfig
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
                            "record_candidates",
                            "record_field_candidates",
                            "selector_stats",
                            "selector_stats_history",
                        ],
                    )
                    self.assertEqual(
                        catalogue.connection.execute(
                            "SELECT * FROM atlas.macros.record_candidates('%')"
                        ).fetchall(),
                        [],
                    )
                    self.assertEqual(
                        catalogue.connection.execute(
                            "SELECT * FROM atlas.macros.record_field_candidates('%', 'x > y')"
                        ).fetchall(),
                        [],
                    )
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
