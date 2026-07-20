"""DuckLake catalogue behind the repository boundary."""

from dataclasses import replace
from tempfile import TemporaryDirectory

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
    ArtifactRecord,
    CatalogueWriteResult,
    CrawlAttemptRecord,
    CrawlRecord,
    CrawlStepRecord,
    DocumentRecord,
    ElementRecord,
    UrlRecord,
)
from repository.catalogue.service import CatalogueBatchEntry, CatalogueService


def catalogue_from_env(
    *,
    threads: int | None = None,
    memory_limit: str | None = None,
):
    """Open an embedded DuckDB connection to Atlas's shared DuckLake catalogue."""

    config = catalogue_config_from_env()
    if threads is not None or memory_limit is not None:
        config = replace(
            config,
            duckdb=replace(
                config.duckdb,
                threads=threads if threads is not None else config.duckdb.threads,
                memory_limit=(
                    memory_limit
                    if memory_limit is not None
                    else config.duckdb.memory_limit
                ),
            ),
        )
    temporary_directory = None
    if config.duckdb.database == ":memory:" and config.duckdb.temp_directory is None:
        temporary_directory = TemporaryDirectory(prefix="atlas-duckdb-")
        config = replace(
            config,
            duckdb=replace(config.duckdb, temp_directory=temporary_directory.name),
        )
    return Catalogue(config, temporary_directory=temporary_directory)


__all__ = [
    "Catalogue",
    "ArtifactRecord",
    "CatalogueBatchEntry",
    "CatalogueConfig",
    "CatalogueConfigError",
    "CatalogueConflictError",
    "CatalogueError",
    "CatalogueService",
    "CatalogueSchemaError",
    "CatalogueValidationError",
    "CatalogueWriteResult",
    "CrawlAttemptRecord",
    "CrawlRecord",
    "CrawlStepRecord",
    "DocumentRecord",
    "ElementRecord",
    "UrlRecord",
    "catalogue_config_from_env",
    "catalogue_from_env",
]
