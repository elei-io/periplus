"""Central SDK DuckLake connection factory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import duckdb

from ._common import (
    quote_identifier,
    quote_literal,
    validate_catalogue,
)
from ._protocol import (
    DuckLakeConnectionProtocol,
    connection_protocol,
)


class DuckConfigLike(Protocol):
    alias: str
    metadata_path: str
    data_path: str
    metadata_schema: str


@dataclass(slots=True)
class DuckLakeConnectionFactory:
    """Create one configured, attached, and validated DuckLake connection."""

    config: DuckConfigLike
    protocol: DuckLakeConnectionProtocol | None = None

    def __post_init__(self) -> None:
        if self.protocol is None:
            self.protocol = connection_protocol(self.config)  # type: ignore[arg-type]

    def connect(
        self,
        *,
        alias: str,
        read_only: bool,
    ) -> duckdb.DuckDBPyConnection:
        connection = duckdb.connect(":memory:")
        try:
            connection.load_extension("ducklake")
            if self.config.metadata_path.startswith("postgres:"):
                connection.load_extension("postgres")
            assert self.protocol is not None
            self.protocol.configure(connection)
            mode = ", READ_ONLY" if read_only else ""
            connection.execute(
                "ATTACH "
                f"{quote_literal('ducklake:' + self.config.metadata_path)} "
                f"AS {quote_identifier(alias)} "
                f"(DATA_PATH {quote_literal(self.config.data_path)}, "
                f"METADATA_SCHEMA {quote_literal(self.config.metadata_schema)}"
                ", OVERRIDE_DATA_PATH true"
                f"{mode})"
            )
            connection.execute(f"USE {quote_identifier(alias)}")
            validate_catalogue(connection)
            return connection
        except BaseException:
            connection.close()
            raise
