from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from control.catalogue_fixtures import (
    seed_catalogue_fixtures,
    seed_system_catalogue_fixtures,
)
from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_queries.models import CatalogueQuery, CatalogueQueryRevision
from control.catalogue_scalar_macros.models import CatalogueScalarMacroDefinition
from control.catalogue_table_macros.models import CatalogueTableMacroDefinition
from control.catalogue_views.models import CatalogueViewReference
from db import Base
from repository.catalogue import Catalogue

FIXTURES_ROOT = Path(__file__).parents[2] / "fixtures"


def seed_system_macros(catalogue: Catalogue) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[CatalogueScalarMacroDefinition.__table__]
    )
    with Session(engine) as session:
        seed_system_catalogue_fixtures(
            session, catalogue, FIXTURES_ROOT
        )
    engine.dispose()


def seed_all_catalogue_fixtures(catalogue: Catalogue) -> None:
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
    with Session(engine) as session:
        seed_catalogue_fixtures(session, catalogue, FIXTURES_ROOT)
    engine.dispose()
