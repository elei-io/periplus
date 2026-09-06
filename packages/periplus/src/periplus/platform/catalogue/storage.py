"""Storage protocols selected by the DuckLake connection factory."""

from __future__ import annotations

import os
from abc import ABC
from pathlib import Path
from urllib.parse import urlsplit

from periplus.platform.catalogue.config import CatalogueConfig, S3StorageConfig


_SECRET_NAME = "_periplus_ducklake_storage"


class DuckLakeStorageProtocol(ABC):
    """All storage-specific connection and immutable-object behavior."""

    def __init__(self, config: CatalogueConfig) -> None:
        self.config = config

    def configure_connection(self, connection) -> None:
        """Prepare a DuckDB connection before its DuckLake attachment."""

    def cli_init_sql(self) -> tuple[str, ...]:
        """Render initialization statements for a protected CLI init file."""

        return ()

    def join(self, *components: str) -> str:
        raise NotImplementedError

    def prepare_root(self) -> None:
        """Prepare the configured data root before DuckLake attachment."""

    def prepare_parent(self, path: str) -> None:
        """Prepare a parent namespace before an immutable object write."""

    def file_size(self, connection, path: str) -> int:
        raise NotImplementedError

    def registration_path(self, path: str) -> str:
        raise NotImplementedError


class FilesystemStorageProtocol(DuckLakeStorageProtocol):
    """Direct POSIX filesystem storage."""

    def prepare_root(self) -> None:
        Path(self.config.data_path).mkdir(parents=True, exist_ok=True)

    def join(self, *components: str) -> str:
        return str(Path(self.config.data_path).joinpath(*components))

    def prepare_parent(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    def file_size(self, _connection, path: str) -> int:
        return Path(path).stat().st_size

    def registration_path(self, path: str) -> str:
        return portable_registration_path(path)


class URIStorageProtocol(DuckLakeStorageProtocol):
    """DuckDB-managed object or network storage."""

    def join(self, *components: str) -> str:
        return "/".join(
            (
                self.config.data_path.rstrip("/"),
                *(component.strip("/") for component in components),
            )
        )

    def file_size(self, connection, path: str) -> int:
        row = connection.execute(
            "SELECT file_size_bytes FROM parquet_file_metadata(?)",
            [path],
        ).fetchone()
        if row is None:
            raise FileNotFoundError(path)
        return int(row[0])

    def registration_path(self, path: str) -> str:
        return path


class S3StorageProtocol(URIStorageProtocol):
    """S3-compatible object storage configured through a scoped secret."""

    def __init__(self, config: CatalogueConfig) -> None:
        super().__init__(config)
        if config.s3 is None:
            raise ValueError("S3 storage protocol requires S3 configuration")
        self.s3 = config.s3

    def configure_connection(self, connection) -> None:
        connection.execute("INSTALL httpfs")
        connection.execute("LOAD httpfs")
        if self.s3.key_id is None:
            connection.execute("INSTALL aws")
            connection.execute("LOAD aws")
        statement, parameters = _s3_secret(self.config.data_path, self.s3)
        connection.execute(statement, parameters)

    def cli_init_sql(self) -> tuple[str, ...]:
        statements = ["INSTALL httpfs; LOAD httpfs;"]
        if self.s3.key_id is None:
            statements.append("INSTALL aws; LOAD aws;")
        statement, parameters = _s3_secret(self.config.data_path, self.s3)
        for parameter in parameters:
            literal = (
                "true" if parameter is True
                else "false" if parameter is False
                else _literal(str(parameter))
            )
            statement = statement.replace("?", literal, 1)
        statements.append(statement + ";")
        return tuple(statements)


_PROTOCOLS = {
    "": FilesystemStorageProtocol,
    "s3": S3StorageProtocol,
}


def storage_protocol(config: CatalogueConfig) -> DuckLakeStorageProtocol:
    """Select one protocol for all connection and object-path behavior."""

    scheme = urlsplit(config.data_path).scheme.lower()
    protocol = _PROTOCOLS.get(scheme, URIStorageProtocol)
    return protocol(config)


def portable_registration_path(path: str) -> str:
    """Keep URIs intact and make direct filesystem names workspace-relative."""

    if urlsplit(path).scheme:
        return path
    relative = os.path.relpath(Path(path).resolve(), Path.cwd().resolve())
    if Path(relative).is_absolute():
        raise ValueError("material registration path must be portable")
    return relative


def _s3_secret(
    data_path: str,
    config: S3StorageConfig,
) -> tuple[str, list[str | bool]]:
    options = [
        "TYPE s3",
        (
            "PROVIDER config"
            if config.key_id is not None
            else "PROVIDER credential_chain"
        ),
    ]
    parameters: list[str | bool] = []
    if config.key_id is not None:
        options.extend(("KEY_ID ?", "SECRET ?"))
        parameters.extend((config.key_id, config.secret_access_key or ""))
    options.append("REGION ?")
    parameters.append(config.region)
    for name, value in (
        ("SESSION_TOKEN", config.session_token),
        ("ENDPOINT", config.endpoint),
        ("URL_STYLE", config.url_style),
    ):
        if value is not None:
            options.append(f"{name} ?")
            parameters.append(value)
    if config.use_ssl is not None:
        options.append("USE_SSL ?")
        parameters.append(config.use_ssl)
    options.append("SCOPE ?")
    parameters.append(data_path)
    return (
        f"CREATE SECRET {_SECRET_NAME} ({', '.join(options)})",
        parameters,
    )


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
