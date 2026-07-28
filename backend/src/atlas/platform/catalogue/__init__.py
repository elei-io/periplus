"""DuckLake catalogue behind the repository boundary."""

from atlas.platform.catalogue.client import Catalogue
from atlas.platform.catalogue.config import CatalogueConfig, catalogue_config_from_env
from atlas.platform.catalogue.duckbasin import (
    DuckBasinAuthenticationError,
    DuckBasinClientMinter,
    DuckBasinCredentialRejectedError,
    DuckBasinError,
    DuckBasinProtocolError,
    DuckBasinUnavailableError,
    ServiceAccountTokenProvider,
)
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
    page_id_for,
)
from atlas.platform.catalogue.service import CatalogueService


def catalogue_from_env(
    *,
    threads: int | None = None,
    memory_limit: str | None = None,
    tokens: ServiceAccountTokenProvider | None = None,
):
    """Mint one session-affine connection to Atlas's managed DuckLake."""

    if threads is not None and threads <= 0:
        raise ValueError("threads must be greater than zero")
    duckdb_config: dict[str, str] = {}
    if threads is not None:
        duckdb_config["threads"] = str(threads)
    if memory_limit is not None:
        duckdb_config["memory_limit"] = memory_limit
    minter = DuckBasinClientMinter(tokens=tokens)
    minted = None
    try:
        minted = minter.mint(duckdb_config=duckdb_config or None)
        config = catalogue_config_from_env(alias=minted.catalogue_alias)
        return Catalogue(
            config,
            minted=minted,
            minter=minter,
            duckdb_config=duckdb_config,
        )
    except BaseException:
        if minted is not None:
            minted.close()
        minter.close()
        raise


__all__ = [
    "Catalogue",
    "AttemptRecord",
    "AtlasProvenance",
    "CatalogueConfig",
    "CatalogueConfigError",
    "CatalogueConflictError",
    "CatalogueError",
    "DuckBasinAuthenticationError",
    "DuckBasinCredentialRejectedError",
    "DuckBasinError",
    "DuckBasinProtocolError",
    "DuckBasinUnavailableError",
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
    "ServiceAccountTokenProvider",
    "attempt_id_for",
    "document_id_for",
    "link_id_for",
    "page_id_for",
    "catalogue_config_from_env",
    "catalogue_from_env",
]
