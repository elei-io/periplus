"""Atlas DuckLake catalogue boundary."""

from catalogue.client import Catalogue
from catalogue.config import CatalogueConfig, catalogue_config_from_env
from catalogue.exceptions import (
    CatalogueConfigError,
    CatalogueConflictError,
    CatalogueError,
    CatalogueSchemaError,
    CatalogueValidationError,
)
from catalogue.records import (
    CatalogueWriteResult,
    CrawlRecord,
    DocumentRecord,
    ElementRecord,
    LinkRecord,
    RunCrawlUsageRecord,
    RunCrawlUsageRole,
    RunManifestRecord,
    RunManifestWriteResult,
)
from catalogue.service import CatalogueBatchEntry, CatalogueService

__all__ = [
    "Catalogue",
    "CatalogueBatchEntry",
    "CatalogueConfig",
    "CatalogueConfigError",
    "CatalogueConflictError",
    "CatalogueError",
    "CatalogueService",
    "CatalogueSchemaError",
    "CatalogueValidationError",
    "CatalogueWriteResult",
    "CrawlRecord",
    "DocumentRecord",
    "ElementRecord",
    "LinkRecord",
    "RunCrawlUsageRecord",
    "RunCrawlUsageRole",
    "RunManifestRecord",
    "RunManifestWriteResult",
    "catalogue_config_from_env",
]
