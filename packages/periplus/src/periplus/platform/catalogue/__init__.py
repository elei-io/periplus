"""DuckLake catalogue boundary with cycle-free lazy exports."""

from __future__ import annotations

from importlib import import_module


_EXPORTS = {
    "Catalogue": ("periplus.platform.catalogue.client", "Catalogue"),
    "CatalogueConfig": (
        "periplus.platform.catalogue.config",
        "CatalogueConfig",
    ),
    "DuckLakeConnectionFactory": (
        "periplus.platform.catalogue.connection",
        "DuckLakeConnectionFactory",
    ),
    "DuckLakeStorageProtocol": (
        "periplus.platform.catalogue.storage",
        "DuckLakeStorageProtocol",
    ),
    "catalogue_config_from_env": (
        "periplus.platform.catalogue.config",
        "catalogue_config_from_env",
    ),
    "CatalogueConfigError": (
        "periplus.platform.catalogue.exceptions",
        "CatalogueConfigError",
    ),
    "CatalogueConflictError": (
        "periplus.platform.catalogue.exceptions",
        "CatalogueConflictError",
    ),
    "CatalogueError": (
        "periplus.platform.catalogue.exceptions",
        "CatalogueError",
    ),
    "CatalogueSchemaError": (
        "periplus.platform.catalogue.exceptions",
        "CatalogueSchemaError",
    ),
    "CatalogueValidationError": (
        "periplus.platform.catalogue.exceptions",
        "CatalogueValidationError",
    ),
    "CatalogueService": (
        "periplus.platform.catalogue.service",
        "CatalogueService",
    ),
}
for _name in (
    "PeriplusProvenance",
    "AttemptRecord",
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
):
    _EXPORTS[_name] = ("periplus.platform.catalogue.records", _name)


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    value = getattr(import_module(target[0]), target[1])
    globals()[name] = value
    return value


def catalogue_from_env(
    *,
    threads: int | None = None,
    memory_limit: str | None = None,
    load_cdc: bool = False,
    read_only: bool = False,
    override_data_path: bool = False,
    protocol=None,
):
    """Open one process-local DuckDB connection to Periplus's DuckLake."""

    if threads is not None and threads <= 0:
        raise ValueError("threads must be greater than zero")
    from periplus.platform.catalogue.client import Catalogue
    from periplus.platform.catalogue.config import catalogue_config_from_env

    duckdb_config: dict[str, str] = {}
    if threads is not None:
        duckdb_config["threads"] = str(threads)
    if memory_limit is not None:
        duckdb_config["memory_limit"] = memory_limit
    return Catalogue(
        catalogue_config_from_env(),
        duckdb_config=duckdb_config,
        load_cdc=load_cdc,
        read_only=read_only,
        override_data_path=override_data_path,
        protocol=protocol,
    )


__all__ = [*_EXPORTS, "catalogue_from_env"]
