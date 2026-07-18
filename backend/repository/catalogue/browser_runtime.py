"""Browser-side Quack runtime configuration derived from Atlas catalogue config."""

from __future__ import annotations

from dataclasses import dataclass

from ducklake_client._attach import build_attach_sql
from ducklake_client import PostgresCatalog

from config import get_str
from repository.catalogue.config import catalogue_config_from_env
from repository.catalogue.schema import CATALOGUE_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class BrowserQuackRuntime:
    uri: str
    token: str
    catalogue_alias: str
    catalogue_schema: str
    metadata_schema: str
    catalogue_schema_version: str
    setup_sql: tuple[str, ...]
    attach_sql: str


def browser_quack_runtime_from_env() -> BrowserQuackRuntime:
    uri = get_str("ATLAS_QUACK_URI")
    token = get_str("ATLAS_QUACK_TOKEN")
    if not uri.startswith("quack:"):
        raise ValueError("ATLAS_QUACK_URI must use the quack: scheme.")

    config = catalogue_config_from_env()
    setup_sql = config.storage.setup_statements(secret_name=f"{config.alias}_storage")
    attach_sql = build_attach_sql(
        catalog=config.catalog,
        storage=config.storage,
        alias=config.alias,
        attach=config.attach,
    )
    return BrowserQuackRuntime(
        uri=uri,
        token=token,
        catalogue_alias=config.alias,
        catalogue_schema=config.schema,
        metadata_schema=(
            "public" if isinstance(config.catalog, PostgresCatalog) else "main"
        ),
        catalogue_schema_version=CATALOGUE_SCHEMA_VERSION,
        setup_sql=setup_sql,
        attach_sql=attach_sql,
    )
