"""Read-only operational facts about the active DuckLake catalogue."""

from dataclasses import dataclass

from repository.catalogue.client import Catalogue


@dataclass(frozen=True, slots=True)
class CatalogueStatus:
    active_file_count: int
    active_storage_bytes: int
    ducklake_version: str | None


def read_catalogue_status(catalogue: Catalogue) -> CatalogueStatus:
    """Return current Atlas-owned Parquet usage and the loaded DuckLake build."""

    metadata_catalog = _quote_identifier(
        f"__ducklake_metadata_{catalogue.config.alias}"
    )
    metadata_schema = _quote_identifier(catalogue.metadata_schema)
    file_stats = catalogue.connection.execute(
        f"""
        SELECT count(*), coalesce(sum(data_file.file_size_bytes), 0)
        FROM {metadata_catalog}.{metadata_schema}.ducklake_data_file AS data_file
        JOIN {metadata_catalog}.{metadata_schema}.ducklake_table AS table_info
          ON table_info.table_id = data_file.table_id
        JOIN {metadata_catalog}.{metadata_schema}.ducklake_schema AS schema_info
          ON schema_info.schema_id = table_info.schema_id
        WHERE data_file.end_snapshot IS NULL
          AND table_info.end_snapshot IS NULL
          AND schema_info.end_snapshot IS NULL
          AND schema_info.schema_name IN (?, '_atlas_materializations')
        """,
        [catalogue.config.schema],
    ).fetchone()
    version = catalogue.connection.execute(
        """
        SELECT extension_version
        FROM duckdb_extensions()
        WHERE extension_name = 'ducklake' AND installed
        """
    ).fetchone()

    return CatalogueStatus(
        active_file_count=int(file_stats[0] if file_stats else 0),
        active_storage_bytes=int(file_stats[1] if file_stats else 0),
        ducklake_version=str(version[0]) if version and version[0] else None,
    )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
