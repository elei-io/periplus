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
    CrawlMaterializationFanout,
    CrawlMaterializationFanoutMember,
    DocumentRecord,
    ElementRecord,
)
from repository.catalogue.service import CatalogueBatchEntry, CatalogueService


def catalogue_from_env():
    """Open an embedded DuckDB connection to Atlas's shared DuckLake catalogue."""

    return Catalogue(catalogue_config_from_env())

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
    "CrawlMaterializationFanout",
    "CrawlMaterializationFanoutMember",
    "DocumentRecord",
    "ElementRecord",
    "catalogue_config_from_env",
    "catalogue_from_env",
]
