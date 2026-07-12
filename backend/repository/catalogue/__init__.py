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
)
from repository.catalogue.service import CatalogueBatchEntry, CatalogueService


def catalogue_from_env():
    """Open Atlas's required remote Quack catalogue boundary."""

    from repository.catalogue.quack import quack_catalogue_from_env

    return quack_catalogue_from_env()

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
    "catalogue_config_from_env",
    "catalogue_from_env",
]
