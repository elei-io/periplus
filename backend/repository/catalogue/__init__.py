"""DuckLake catalogue behind the repository boundary."""

from repository.catalogue.client import Catalogue
from repository.catalogue.config import CatalogueConfig, catalogue_config_from_env
from repository.catalogue.duckbasin import DuckBasinClientMinter
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
    """Mint one session-affine connection to Atlas's managed DuckLake."""

    if threads is not None and threads <= 0:
        raise ValueError("threads must be greater than zero")
    config = catalogue_config_from_env()
    duckdb_config: dict[str, str] = {}
    if threads is not None:
        duckdb_config["threads"] = str(threads)
    if memory_limit is not None:
        duckdb_config["memory_limit"] = memory_limit
    minter = DuckBasinClientMinter()
    try:
        minted = minter.mint(duckdb_config=duckdb_config or None)
    except BaseException:
        minter.close()
        raise
    return Catalogue(
        config,
        minted=minted,
        minter=minter,
        duckdb_config=duckdb_config,
    )


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
