"""Central DuckLake connection factory."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import duckdb

from periplus.platform.catalogue.cdc_extension import load_cdc_extension
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.config.duckdb import connection_limits
from periplus.platform.catalogue.storage import (
    DuckLakeStorageProtocol,
    storage_protocol,
)


@dataclass(slots=True)
class DuckLakeConnectionFactory:
    """Create equivalent Periplus DuckLake connections for every process role."""

    config: CatalogueConfig
    duckdb_config: Mapping[str, str] | None = None
    protocol: DuckLakeStorageProtocol | None = None
    storage: DuckLakeStorageProtocol = field(init=False)

    def __post_init__(self) -> None:
        self.storage = self.protocol or storage_protocol(self.config)

    def connect(
        self,
        *,
        load_cdc: bool = False,
        read_only: bool = False,
        override_data_path: bool = False,
    ) -> duckdb.DuckDBPyConnection:
        connection_config = connection_limits(self.duckdb_config)
        connection_config["allow_unsigned_extensions"] = "false"
        connection = duckdb.connect(":memory:", config=connection_config)
        try:
            connection.execute("INSTALL ducklake")
            connection.execute("LOAD ducklake")
            if self.config.metadata_path.startswith("postgres:"):
                connection.execute("INSTALL postgres")
                connection.execute("LOAD postgres")
            self.storage.configure_connection(connection)
            if load_cdc:
                load_cdc_extension(connection)
            if not read_only:
                self.storage.prepare_root()
            connection.execute(
                self.attach_sql(
                    read_only=read_only,
                    override_data_path=override_data_path,
                )
            )
            return connection
        except BaseException:
            connection.close()
            raise

    def cli_init_sql(
        self,
        *,
        read_only: bool,
        override_data_path: bool,
    ) -> str:
        statements = [
            "INSTALL ducklake; LOAD ducklake;",
            (
                "INSTALL postgres; LOAD postgres;"
                if self.config.metadata_path.startswith("postgres:")
                else ""
            ),
            *self.storage.cli_init_sql(),
            self.attach_sql(
                read_only=read_only,
                override_data_path=override_data_path,
            )
            + ";",
            f"USE {_identifier(self.config.alias)};",
        ]
        return "\n".join(statements)

    def attach_sql(
        self,
        *,
        read_only: bool = False,
        override_data_path: bool = False,
    ) -> str:
        options = [
            f"DATA_PATH {_literal(self.config.data_path)}",
            f"METADATA_SCHEMA {_literal(self.config.metadata_schema)}",
        ]
        if override_data_path:
            options.append("OVERRIDE_DATA_PATH true")
        if read_only:
            options.append("READ_ONLY")
        return (
            f"ATTACH {_literal('ducklake:' + self.config.metadata_path)} "
            f"AS {_identifier(self.config.alias)} "
            f"({', '.join(options)})"
        )


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
