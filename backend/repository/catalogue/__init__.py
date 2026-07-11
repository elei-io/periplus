"""DuckLake catalogue behind the repository boundary."""

from repository.catalogue.client import Catalogue
from repository.catalogue.config import CatalogueConfig, catalogue_config_from_env
from repository.catalogue.exceptions import (
    CatalogueConfigError,
    CatalogueConflictError,
    CatalogueError,
    CatalogueSchemaError,
    CatalogueValidationError,
)
from repository.catalogue.records import (
    CatalogueWriteResult,
    CrawlRecord,
    DocumentRecord,
    ElementRecord,
    LinkRecord,
)
from repository.catalogue.service import CatalogueBatchEntry, CatalogueService

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
