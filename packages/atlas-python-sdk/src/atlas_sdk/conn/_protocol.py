"""Storage protocols used by the SDK DuckLake connection factory."""

from __future__ import annotations

from abc import ABC
from typing import Protocol
from urllib.parse import urlsplit

import duckdb


class S3ConfigLike(Protocol):
    endpoint: str | None
    region: str
    key_id: str | None
    secret_access_key: str | None
    session_token: str | None
    url_style: str | None
    use_ssl: bool | None


class DuckConfigLike(Protocol):
    data_path: str
    s3: S3ConfigLike | None


class DuckLakeConnectionProtocol(ABC):
    """Configure one DuckDB connection for a DuckLake storage protocol."""

    def __init__(self, config: DuckConfigLike) -> None:
        self.config = config

    def configure(self, connection: duckdb.DuckDBPyConnection) -> None:
        """Prepare storage access before DuckLake is attached."""


class FilesystemConnectionProtocol(DuckLakeConnectionProtocol):
    """Direct filesystem access needs no extra DuckDB configuration."""


class URIConnectionProtocol(DuckLakeConnectionProtocol):
    """Externally configured DuckDB URI storage."""


class S3ConnectionProtocol(URIConnectionProtocol):
    """S3-compatible storage configured with one scoped secret."""

    def __init__(self, config: DuckConfigLike) -> None:
        super().__init__(config)
        if config.s3 is None:
            raise ValueError("S3 connection protocol requires S3 configuration")
        self.s3 = config.s3

    def configure(self, connection: duckdb.DuckDBPyConnection) -> None:
        connection.load_extension("httpfs")
        if self.s3.key_id is None:
            connection.load_extension("aws")
        options = [
            "TYPE s3",
            (
                "PROVIDER config"
                if self.s3.key_id is not None
                else "PROVIDER credential_chain"
            ),
        ]
        parameters: list[str | bool] = []
        if self.s3.key_id is not None:
            options.extend(("KEY_ID ?", "SECRET ?"))
            parameters.extend(
                (self.s3.key_id, self.s3.secret_access_key or "")
            )
        options.append("REGION ?")
        parameters.append(self.s3.region)
        for name, value in (
            ("SESSION_TOKEN", self.s3.session_token),
            ("ENDPOINT", self.s3.endpoint),
            ("URL_STYLE", self.s3.url_style),
        ):
            if value is not None:
                options.append(f"{name} ?")
                parameters.append(value)
        if self.s3.use_ssl is not None:
            options.append("USE_SSL ?")
            parameters.append(self.s3.use_ssl)
        options.append("SCOPE ?")
        parameters.append(self.config.data_path)
        connection.execute(
            "CREATE SECRET _atlas_ducklake_storage "
            f"({', '.join(options)})",
            parameters,
        )


_PROTOCOLS = {
    "": FilesystemConnectionProtocol,
    "s3": S3ConnectionProtocol,
}


def connection_protocol(
    config: DuckConfigLike,
) -> DuckLakeConnectionProtocol:
    """Select the storage protocol once at the connection boundary."""

    scheme = urlsplit(config.data_path).scheme.lower()
    protocol = _PROTOCOLS.get(scheme, URIConnectionProtocol)
    return protocol(config)
