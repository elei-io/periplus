"""DuckLake catalogue behind the repository boundary."""

from atlas.platform.catalogue.client import Catalogue
from atlas.platform.catalogue.config import CatalogueConfig, catalogue_config_from_env
from atlas.platform.catalogue.exceptions import (
    CatalogueConfigError,
    CatalogueConflictError,
    CatalogueError,
    CatalogueSchemaError,
    CatalogueValidationError,
)
from atlas.platform.catalogue.records import (
    AtlasProvenance,
    AttemptRecord,
    CrawlRecord,
    DocumentRecord,
    EvidenceProvenance,
    ExternalProvenance,
    IngestionWriteResult,
    StepRecord,
    VisitEvidence,
    VisitRecord,
    attempt_id_for,
    document_id_for,
    link_id_for,
    link_occurrence_id_for,
    page_id_for,
)
from atlas.platform.catalogue.service import CatalogueService


def catalogue_from_env(
    *,
    threads: int | None = None,
    memory_limit: str | None = None,
):
    """Open one process-local DuckDB connection to Atlas's DuckLake."""

    if threads is not None and threads <= 0:
        raise ValueError("threads must be greater than zero")
    duckdb_config: dict[str, str] = {}
    if threads is not None:
        duckdb_config["threads"] = str(threads)
    if memory_limit is not None:
        duckdb_config["memory_limit"] = memory_limit
    return Catalogue(
        catalogue_config_from_env(),
        duckdb_config=duckdb_config,
    )


__all__ = [
    "Catalogue",
    "AttemptRecord",
    "AtlasProvenance",
    "CatalogueConfig",
    "CatalogueConfigError",
    "CatalogueConflictError",
    "CatalogueError",
    "CatalogueService",
    "CatalogueSchemaError",
    "CatalogueValidationError",
    "CrawlRecord",
    "DocumentRecord",
    "EvidenceProvenance",
    "ExternalProvenance",
    "IngestionWriteResult",
    "StepRecord",
    "VisitEvidence",
    "VisitRecord",
    "attempt_id_for",
    "document_id_for",
    "link_id_for",
    "link_occurrence_id_for",
    "page_id_for",
    "catalogue_config_from_env",
    "catalogue_from_env",
]
