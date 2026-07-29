"""Quack-backed DuckDB connection."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets
from typing import Any, Self
from uuid import UUID

import duckdb
import httpx

from atlas_sdk.errors import (
    AtlasConnectionError,
    AuthenticationError,
    ConfigurationError,
)

from ._common import (
    ExtensionMode,
    QueryProfile,
    load_atlas_extension,
    quote_identifier,
    quote_literal,
    validate_catalogue,
)


@dataclass(frozen=True, slots=True)
class QuackCredentials:
    token_endpoint: str
    client_id: str
    client_secret: str = field(repr=False)

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            token_endpoint=_env("DUCKBASIN_TOKEN_ENDPOINT"),
            client_id=_env("DUCKBASIN_CLIENT_ID"),
            client_secret=_env("DUCKBASIN_CLIENT_SECRET"),
        )


@dataclass(frozen=True, slots=True)
class QuackConfig:
    endpoint: str
    lake: str
    credentials: QuackCredentials = field(repr=False)
    request_timeout_seconds: float = 15

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            endpoint=_env("DUCKBASIN_URL").rstrip("/"),
            lake=_env("DUCKBASIN_LAKE"),
            credentials=QuackCredentials.from_env(),
            request_timeout_seconds=float(
                os.getenv("DUCKBASIN_REQUEST_TIMEOUT_SECONDS", "15")
            ),
        )


@dataclass(frozen=True, slots=True)
class _Target:
    lake_id: UUID
    alias: str
    uri: str
    scope: str
    disable_ssl: bool


def quack(
    config: QuackConfig | None = None,
    *,
    lake: str | None = None,
    endpoint: str | None = None,
    credentials: QuackCredentials | None = None,
    read_only: bool = True,
    profile: QueryProfile = "interactive",
    extension: ExtensionMode = "auto",
    extension_path: str | Path | None = None,
) -> duckdb.DuckDBPyConnection:
    """Attach one Atlas lake through Quack and return its DuckDB connection."""

    base = config or QuackConfig.from_env()
    resolved = QuackConfig(
        endpoint=(endpoint or base.endpoint).rstrip("/"),
        lake=lake or base.lake,
        credentials=credentials or base.credentials,
        request_timeout_seconds=base.request_timeout_seconds,
    )
    atlas_path = extension_path or os.getenv("ATLAS_DUCKDB_EXTENSION_PATH")
    with httpx.Client(timeout=resolved.request_timeout_seconds) as client:
        token = _token(client, resolved.credentials)
        target = _target(client, resolved, token)
    session_id = secrets.token_hex(8)
    target = _horizontalize(target, session_id)
    connection = duckdb.connect(
        ":memory:",
        config={
            "threads": "1",
            "allow_unsigned_extensions": (
                "true" if atlas_path is not None else "false"
            ),
        },
    )
    try:
        connection.load_extension("quack")
        connection.execute(
            "CREATE TEMPORARY SECRET _atlas_duckbasin_auth ("
            "TYPE quack, "
            f"TOKEN {quote_literal(token)}, "
            f"SCOPE {quote_literal(target.scope)}"
            ")"
        )
        options = "TYPE quack"
        if target.disable_ssl:
            options += ", DISABLE_SSL true"
        if read_only:
            options += ", READ_ONLY"
        connection.execute(
            f"ATTACH {quote_literal(target.uri)} "
            f"AS {quote_identifier(target.alias)} ({options})"
        )
        connection.execute(f"USE {quote_identifier(target.alias)}")
        loaded = load_atlas_extension(
            connection, mode=extension, path=atlas_path
        )
        validate_catalogue(
            connection,
            profile=profile,
            extension_loaded=loaded,
        )
        return connection
    except BaseException as exc:
        connection.close()
        if isinstance(exc, duckdb.Error):
            raise AtlasConnectionError(
                "Quack DuckDB connection could not be established"
            ) from None
        raise


def _token(client: httpx.Client, credentials: QuackCredentials) -> str:
    try:
        response = client.post(
            credentials.token_endpoint,
            data={
                "grant_type": "client_credentials",
                "scope": "duckbasin",
            },
            auth=(credentials.client_id, credentials.client_secret),
        )
    except httpx.HTTPError as exc:
        raise AtlasConnectionError(
            "DuckBasin token service is unavailable"
        ) from exc
    if response.status_code in {401, 403}:
        raise AuthenticationError(
            "DuckBasin rejected the service-account credentials"
        )
    if response.is_error:
        raise AtlasConnectionError(
            f"DuckBasin token service failed (HTTP {response.status_code})"
        )
    try:
        value = response.json()["access_token"]
    except (KeyError, TypeError, ValueError) as exc:
        raise AtlasConnectionError(
            "DuckBasin token service returned an invalid response"
        ) from exc
    if not isinstance(value, str) or not value:
        raise AtlasConnectionError(
            "DuckBasin token service returned an invalid response"
        )
    return value


def _target(
    client: httpx.Client,
    config: QuackConfig,
    token: str,
) -> _Target:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        lakes_response = client.get(
            f"{config.endpoint}/api/ducklakes/", headers=headers
        )
    except httpx.HTTPError as exc:
        raise AtlasConnectionError("DuckBasin API is unavailable") from exc
    if lakes_response.status_code in {401, 403}:
        raise AuthenticationError("DuckBasin rejected the access token")
    if lakes_response.is_error:
        raise AtlasConnectionError(
            f"DuckBasin lake lookup failed (HTTP {lakes_response.status_code})"
        )
    try:
        lakes: Any = lakes_response.json()
        matches = [
            item
            for item in lakes
            if isinstance(item, dict)
            and (
                item.get("slug") == config.lake
                or item.get("id") == config.lake
            )
        ]
        if len(matches) != 1 or not matches[0].get("connectable"):
            raise ValueError
        lake = matches[0]
        lake_id = UUID(str(lake["id"]))
    except (TypeError, KeyError, ValueError) as exc:
        raise AtlasConnectionError(
            "DuckBasin lake was not uniquely connectable"
        ) from exc
    try:
        response = client.get(
            f"{config.endpoint}/api/ducklakes/{lake_id}/connection/",
            headers=headers,
        )
    except httpx.HTTPError as exc:
        raise AtlasConnectionError("DuckBasin API is unavailable") from exc
    if response.status_code in {401, 403}:
        raise AuthenticationError("DuckBasin rejected the access token")
    if response.is_error:
        raise AtlasConnectionError(
            f"DuckBasin connection lookup failed (HTTP {response.status_code})"
        )
    try:
        payload = response.json()
        target_lake_id = UUID(str(payload["id"]))
        alias = payload["catalog_alias"]
        uri = payload["quack_uri"]
        scope = payload["secret_scope"]
        disable_ssl = payload["disable_ssl"]
        if (
            target_lake_id != lake_id
            or not isinstance(alias, str)
            or not alias
            or not isinstance(uri, str)
            or not uri
            or not isinstance(scope, str)
            or not scope
            or not isinstance(disable_ssl, bool)
        ):
            raise ValueError
        target = _Target(
            lake_id=target_lake_id,
            alias=alias,
            uri=uri,
            scope=scope,
            disable_ssl=disable_ssl,
        )
    except (TypeError, KeyError, ValueError) as exc:
        raise AtlasConnectionError(
            "DuckBasin returned an invalid connection target"
        ) from exc
    _horizontalize(target, "validation")
    return target


def _horizontalize(target: _Target, session_id: str) -> _Target:
    lake_hex = target.lake_id.hex
    prefix = f"quack:{lake_hex}."

    def rewrite(value: str) -> str:
        if not value.startswith(prefix):
            raise AtlasConnectionError(
                "DuckBasin target has a non-canonical lake identity"
            )
        return f"quack:{session_id}-{value.removeprefix('quack:')}"

    return _Target(
        lake_id=target.lake_id,
        alias=target.alias,
        uri=rewrite(target.uri),
        scope=rewrite(target.scope),
        disable_ssl=target.disable_ssl,
    )


def _env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value
