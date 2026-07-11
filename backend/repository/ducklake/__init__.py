"""Private DuckLake implementation for the Atlas repository."""

from repository.ducklake.client import Catalogue
from repository.ducklake.config import CatalogueConfig, catalogue_config_from_env
from repository.ducklake.exceptions import (
    CatalogueConfigError,
    CatalogueConflictError,
    CatalogueError,
    CatalogueSchemaError,
    CatalogueValidationError,
)
from repository.ducklake.records import (
    CatalogueWriteResult,
    CrawlRecord,
    DocumentRecord,
    ElementRecord,
    LinkRecord,
)
from repository.ducklake.service import CatalogueBatchEntry, CatalogueService

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
    "catalogue_config_from_env",
]
