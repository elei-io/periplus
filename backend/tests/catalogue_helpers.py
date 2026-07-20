from pathlib import Path
from datetime import UTC, datetime
from uuid import UUID

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
from repository.catalogue import Catalogue, CrawlAttemptRecord, UrlRecord

FIXTURES_ROOT = Path(__file__).parents[2] / "fixtures"


def crawl_url_evidence(
    crawl_id: UUID,
    url: str = "https://example.com/",
    *,
    captured_at: datetime | None = None,
    final_url: str | None = None,
    outcome: str = "success",
) -> tuple[UrlRecord, tuple[UrlRecord, ...], tuple[CrawlAttemptRecord, ...]]:
    started_at = captured_at or datetime.now(UTC)
    requested = UrlRecord.from_normalized_url(url)
    final = UrlRecord.from_normalized_url(final_url) if final_url is not None else None
    urls = tuple(
        {value.url_id: value for value in (requested, final) if value is not None}.values()
    )
    attempt = CrawlAttemptRecord(
        crawl_id=crawl_id,
        attempt_number=1,
        started_at=started_at,
        completed_at=started_at,
        requested_url_id=requested.url_id,
        final_url_id=final.url_id if final is not None else None,
        status_code=200 if outcome == "success" else None,
        outcome=outcome,
        failure_code=None if outcome == "success" else "expected_failure",
    )
    return requested, urls, (attempt,)


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
