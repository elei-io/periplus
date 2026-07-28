"""Mint session-affine DuckDB clients for a DuckBasin-managed DuckLake."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import random
import secrets
import threading
import time
from typing import Any
from uuid import UUID

import duckdb
import httpx

from atlas.platform.config import get_float, get_str


_SESSION_HEX_LENGTH = 16
_OAUTH_SCOPE = "duckbasin"
_SECRET_NAME = "_atlas_duckbasin_auth"


class DuckBasinError(RuntimeError):
    """A DuckBasin client could not be configured or minted."""


class DuckBasinAuthenticationError(DuckBasinError):
    """DuckBasin rejected the configured service-account credentials."""


class DuckBasinProtocolError(DuckBasinError):
    """DuckBasin returned a successful but invalid control-plane response."""


class DuckBasinUnavailableError(DuckBasinError):
    """DuckBasin or Quack is transiently unavailable."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class DuckBasinCredentialRejectedError(DuckBasinUnavailableError):
    """Quack rejected a provider-issued credential that may be refreshed."""


@dataclass(frozen=True, slots=True)
class DuckBasinConfig:
    """OAuth client credentials and lake selection needed by the minter."""

    base_url: str
    lake: str
    token_endpoint: str
    client_id: str
    client_secret: str = field(repr=False)
    request_timeout_seconds: float = 15
    token_refresh_seconds: float = 60

    @classmethod
    def from_env(cls) -> DuckBasinConfig:
        return cls(
            base_url=get_str("DUCKBASIN_URL").rstrip("/"),
            lake=get_str("DUCKBASIN_LAKE"),
            token_endpoint=get_str("DUCKBASIN_TOKEN_ENDPOINT"),
            client_id=get_str("DUCKBASIN_CLIENT_ID"),
            client_secret=get_str("DUCKBASIN_CLIENT_SECRET"),
            request_timeout_seconds=get_float(
                "DUCKBASIN_REQUEST_TIMEOUT_SECONDS"
            ),
            token_refresh_seconds=get_float(
                "DUCKBASIN_TOKEN_REFRESH_SECONDS"
            ),
        )


@dataclass(frozen=True, slots=True)
class DuckBasinToken:
    """One cached access-token generation."""

    value: str = field(repr=False)
    generation: int


class ServiceAccountTokenProvider:
    """Thread-safe, single-flight OAuth client-credentials token provider."""

    def __init__(
        self,
        config: DuckBasinConfig,
        *,
        client: httpx.Client | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        random_value: Callable[[], float] = random.random,
        retry_initial_seconds: float = 0.5,
        retry_max_seconds: float = 30.0,
    ) -> None:
        if retry_initial_seconds <= 0 or retry_max_seconds < retry_initial_seconds:
            raise ValueError("token retry bounds are invalid")
        self._config = config
        self._client = client or httpx.Client(
            timeout=config.request_timeout_seconds
        )
        self._owns_client = client is None
        self._monotonic = monotonic
        self._random_value = random_value
        self._retry_initial_seconds = retry_initial_seconds
        self._retry_max_seconds = retry_max_seconds
        self._condition = threading.Condition()
        self._token: DuckBasinToken | None = None
        self._expires_monotonic = 0.0
        self._refreshing = False
        self._generation = 0
        self._failure: DuckBasinError | None = None
        self._retry_monotonic = 0.0
        self._consecutive_failures = 0

    def get(self) -> DuckBasinToken:
        while True:
            with self._condition:
                now = self._monotonic()
                if self._token is not None and now < self._expires_monotonic:
                    return self._token
                if (
                    self._failure is not None
                    and now < self._retry_monotonic
                ):
                    raise _copy_duckbasin_error(self._failure)
                if not self._refreshing:
                    self._refreshing = True
                    break
                self._condition.wait()

        try:
            try:
                token, lifetime = self._request_token()
            except DuckBasinError as exc:
                now = self._monotonic()
                with self._condition:
                    self._consecutive_failures += 1
                    exponential = min(
                        self._retry_max_seconds,
                        self._retry_initial_seconds
                        * (2 ** min(10, self._consecutive_failures - 1)),
                    )
                    jittered = exponential * (
                        0.8 + 0.4 * self._random_value()
                    )
                    retry_after = (
                        exc.retry_after_seconds
                        if isinstance(exc, DuckBasinUnavailableError)
                        else None
                    )
                    delay = max(jittered, retry_after or 0.0)
                    self._failure = exc
                    self._retry_monotonic = now + delay
                raise
            now = self._monotonic()
            refresh_margin = min(
                self._config.token_refresh_seconds,
                lifetime / 2,
            )
            with self._condition:
                self._generation += 1
                lease = DuckBasinToken(
                    value=token,
                    generation=self._generation,
                )
                self._token = lease
                self._expires_monotonic = now + lifetime - refresh_margin
                self._failure = None
                self._retry_monotonic = 0.0
                self._consecutive_failures = 0
                return lease
        finally:
            with self._condition:
                self._refreshing = False
                self._condition.notify_all()

    def invalidate(self, token: DuckBasinToken) -> None:
        with self._condition:
            if self._token is token:
                self._token = None
                self._expires_monotonic = 0
                self._failure = None
                self._retry_monotonic = 0.0

    def invalidate_generation(self, generation: int) -> None:
        """Discard a credential generation rejected by Quack."""

        with self._condition:
            if (
                self._token is not None
                and self._token.generation == generation
            ):
                self._token = None
                self._expires_monotonic = 0
                self._failure = None
                self._retry_monotonic = 0.0

    def status(self) -> tuple[str, int]:
        """Return cached token state without triggering a token refresh."""

        with self._condition:
            generation = self._generation
            now = self._monotonic()
            if self._refreshing:
                return "refreshing", generation
            if self._failure is not None:
                return (
                    "backoff" if now < self._retry_monotonic else "failed",
                    generation,
                )
            if self._token is None:
                return "empty", generation
            if now >= self._expires_monotonic:
                return "expired", generation
            return "valid", self._token.generation

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request_token(self) -> tuple[str, float]:
        try:
            response = self._client.post(
                self._config.token_endpoint,
                data={
                    "grant_type": "client_credentials",
                    "scope": _OAUTH_SCOPE,
                },
                auth=(self._config.client_id, self._config.client_secret),
            )
        except httpx.HTTPError as exc:
            raise DuckBasinUnavailableError(
                "DuckBasin token service is unavailable"
            ) from exc
        if response.status_code in {401, 403}:
            raise DuckBasinAuthenticationError(
                "DuckBasin rejected the service-account credentials "
                f"(HTTP {response.status_code})"
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise DuckBasinUnavailableError(
                "DuckBasin token service is unavailable "
                f"(HTTP {response.status_code})",
                retry_after_seconds=_retry_after_seconds(response),
            )
        if response.is_error:
            raise DuckBasinProtocolError(
                "DuckBasin token service rejected the request "
                f"(HTTP {response.status_code})"
            )
        try:
            payload = response.json()
            token = payload["access_token"]
            lifetime = float(payload["expires_in"])
            if not isinstance(token, str) or not token:
                raise ValueError("access_token is empty")
            if lifetime <= 0:
                raise ValueError("expires_in is not positive")
            return token, lifetime
        except (KeyError, TypeError, ValueError) as exc:
            raise DuckBasinProtocolError(
                "DuckBasin token service returned an invalid response"
            ) from exc


@dataclass(frozen=True, slots=True)
class DuckBasinTarget:
    """The stable Basin lake target before session affinity is added."""

    lake_id: UUID
    lake_slug: str
    catalogue_alias: str
    quack_uri: str
    quack_scope: str
    disable_ssl: bool

    def horizontal(self, session_id: str) -> DuckBasinTarget:
        _validate_session_id(session_id)
        lake_hex = self.lake_id.hex
        return DuckBasinTarget(
            lake_id=self.lake_id,
            lake_slug=self.lake_slug,
            catalogue_alias=self.catalogue_alias,
            quack_uri=_horizontalize(self.quack_uri, lake_hex, session_id),
            quack_scope=_horizontalize(
                self.quack_scope,
                lake_hex,
                session_id,
            ),
            disable_ssl=self.disable_ssl,
        )


@dataclass(slots=True)
class MintedDuckDB:
    """An owned DuckDB connection pinned to one Basin routing session."""

    connection: duckdb.DuckDBPyConnection = field(repr=False)
    session_id: str
    lake_slug: str
    catalogue_alias: str
    quack_uri: str
    token_generation: int
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self.connection.close()
        self._closed = True

    def __enter__(self) -> MintedDuckDB:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class DuckBasinClientMinter:
    """Discover one Basin lake and mint isolated, session-affine clients."""

    def __init__(
        self,
        config: DuckBasinConfig | None = None,
        *,
        client: httpx.Client | None = None,
        tokens: ServiceAccountTokenProvider | None = None,
        connect: Callable[..., duckdb.DuckDBPyConnection] = duckdb.connect,
        session_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.config = config or DuckBasinConfig.from_env()
        self._client = client or httpx.Client(
            timeout=self.config.request_timeout_seconds
        )
        self._owns_client = client is None
        self.tokens = tokens or ServiceAccountTokenProvider(
            self.config,
            client=self._client,
        )
        self._owns_tokens = tokens is None
        self._connect = connect
        self._session_id_factory = session_id_factory or (
            lambda: secrets.token_hex(_SESSION_HEX_LENGTH // 2)
        )
        self._target_condition = threading.Condition()
        self._target: DuckBasinTarget | None = None
        self._resolving_target = False

    def target(self) -> DuckBasinTarget:
        while True:
            with self._target_condition:
                if self._target is not None:
                    return self._target
                if not self._resolving_target:
                    self._resolving_target = True
                    break
                self._target_condition.wait()
        try:
            target = self._resolve_target()
            with self._target_condition:
                self._target = target
                return target
        finally:
            with self._target_condition:
                self._resolving_target = False
                self._target_condition.notify_all()

    def mint(
        self,
        database: str = ":memory:",
        *,
        duckdb_config: Mapping[str, str] | None = None,
    ) -> MintedDuckDB:
        target = self.target()
        for attempt in range(2):
            token = self.tokens.get()
            session_id = self._session_id_factory()
            _validate_session_id(session_id)
            horizontal = target.horizontal(session_id)
            connection = self._connect(
                database,
                config=dict(duckdb_config or {"threads": "1"}),
            )
            try:
                connection.load_extension("quack")
                connection.execute(
                    "CREATE TEMPORARY SECRET "
                    f"{_quote_identifier(_SECRET_NAME)} ("
                    "TYPE quack, "
                    f"TOKEN {_quote_literal(token.value)}, "
                    f"SCOPE {_quote_literal(horizontal.quack_scope)}"
                    ")"
                )
                options = "TYPE quack"
                if horizontal.disable_ssl:
                    options += ", DISABLE_SSL true"
                connection.execute(
                    f"ATTACH {_quote_literal(horizontal.quack_uri)} "
                    f"AS {_quote_identifier(horizontal.catalogue_alias)} "
                    f"({options})"
                )
                connection.execute(
                    f"USE {_quote_identifier(horizontal.catalogue_alias)}"
                )
            except BaseException as exc:
                connection.close()
                classified = classify_quack_connection_error(exc)
                if isinstance(classified, DuckBasinCredentialRejectedError):
                    self.tokens.invalidate(token)
                    if attempt == 0:
                        continue
                if classified is not None:
                    raise classified from exc
                raise
            return MintedDuckDB(
                connection=connection,
                session_id=session_id,
                lake_slug=horizontal.lake_slug,
                catalogue_alias=horizontal.catalogue_alias,
                quack_uri=horizontal.quack_uri,
                token_generation=token.generation,
            )
        raise AssertionError("unreachable")

    def connection_credentials_stale(self, minted: MintedDuckDB) -> bool:
        """Return whether a connection predates the provider's current token."""

        return self.tokens.get().generation != minted.token_generation

    def token_status(self) -> tuple[str, int]:
        """Return token telemetry without refreshing credentials."""

        return self.tokens.status()

    def invalidate_connection_credentials(self, minted: MintedDuckDB) -> None:
        """Invalidate the token generation rejected by a remote connection."""

        self.tokens.invalidate_generation(minted.token_generation)

    def close(self) -> None:
        if self._owns_tokens:
            self.tokens.close()
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> DuckBasinClientMinter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _resolve_target(self) -> DuckBasinTarget:
        lakes = self._authorized_get("/api/ducklakes/")
        if not isinstance(lakes, list):
            raise DuckBasinError("DuckBasin returned an invalid lake list")
        lake = self._select_lake(lakes)
        if not lake.get("connectable"):
            raise DuckBasinError(
                f"DuckBasin lake {self.config.lake!r} is not connectable"
            )
        lake_id = UUID(str(lake["id"]))
        payload = self._authorized_get(
            f"/api/ducklakes/{lake_id}/connection/"
        )
        if not isinstance(payload, dict):
            raise DuckBasinError("DuckBasin returned an invalid connection target")
        try:
            disable_ssl = payload["disable_ssl"]
            if not isinstance(disable_ssl, bool):
                raise TypeError("disable_ssl is not a boolean")
            target = DuckBasinTarget(
                lake_id=UUID(str(payload["id"])),
                lake_slug=str(lake["slug"]),
                catalogue_alias=str(payload["catalog_alias"]),
                quack_uri=str(payload["quack_uri"]),
                quack_scope=str(payload["secret_scope"]),
                disable_ssl=disable_ssl,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DuckBasinError(
                "DuckBasin returned an invalid connection target"
            ) from exc
        if target.lake_id != lake_id:
            raise DuckBasinError("DuckBasin connection target changed lake identity")
        if not target.lake_slug:
            raise DuckBasinError("DuckBasin returned an empty lake slug")
        target.horizontal("0" * _SESSION_HEX_LENGTH)
        return target

    def _select_lake(self, lakes: list[Any]) -> Mapping[str, Any]:
        matches = [
            lake
            for lake in lakes
            if isinstance(lake, dict)
            and (
                lake.get("slug") == self.config.lake
                or lake.get("id") == self.config.lake
            )
        ]
        if len(matches) != 1:
            raise DuckBasinError(
                f"DuckBasin lake {self.config.lake!r} was not uniquely available"
            )
        return matches[0]

    def _authorized_get(self, path: str) -> Any:
        token = self.tokens.get()
        response = self._get(path, token)
        if response.status_code == 401:
            self.tokens.invalidate(token)
            token = self.tokens.get()
            response = self._get(path, token)
        if response.status_code in {401, 403}:
            raise DuckBasinAuthenticationError(
                "DuckBasin rejected the service-account credentials "
                f"(HTTP {response.status_code})"
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise DuckBasinUnavailableError(
                f"DuckBasin API is unavailable for {path} "
                f"(HTTP {response.status_code})",
                retry_after_seconds=_retry_after_seconds(response),
            )
        if response.is_error:
            raise DuckBasinError(
                f"DuckBasin API rejected {path} "
                f"(HTTP {response.status_code})"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise DuckBasinProtocolError(
                f"DuckBasin API returned invalid JSON for {path}"
            ) from exc

    def _get(
        self,
        path: str,
        token: DuckBasinToken,
    ) -> httpx.Response:
        try:
            return self._client.get(
                f"{self.config.base_url}{path}",
                headers={"Authorization": f"Bearer {token.value}"},
            )
        except httpx.HTTPError as exc:
            raise DuckBasinUnavailableError(
                f"DuckBasin API is unavailable for {path}"
            ) from exc


def _horizontalize(value: str, lake_hex: str, session_id: str) -> str:
    prefix = f"quack:{lake_hex}."
    if not value.startswith(prefix):
        raise DuckBasinError(
            "DuckBasin target does not start with its canonical lake identity"
        )
    return f"quack:{session_id}-{value.removeprefix('quack:')}"


def _validate_session_id(session_id: str) -> None:
    if (
        len(session_id) != _SESSION_HEX_LENGTH
        or any(character not in "0123456789abcdef" for character in session_id)
    ):
        raise DuckBasinError(
            "DuckBasin session IDs must contain exactly 16 lowercase hex characters"
        )


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def classify_quack_connection_error(
    exc: BaseException,
) -> DuckBasinUnavailableError | None:
    """Translate Quack transport/session failures at the repository boundary."""

    message = str(exc).lower()
    if "authentication failed" in message or "authorization failed" in message:
        return DuckBasinCredentialRejectedError(
            "Quack rejected the DuckBasin credential"
        )
    transient_fragments = (
        "invalid connection",
        "timeout",
        "connection reset",
        "connection refused",
        "connection closed",
        "connection error",
        "failed to connect",
        "failed to send message",
        "error sending request",
        "status 429",
        "status code 429",
        "http 429",
        "status 500",
        "status code 500",
        "http 500",
        "status 502",
        "status code 502",
        "http 502",
        "status 503",
        "status code 503",
        "http 503",
        "status 504",
        "status code 504",
        "http 504",
    )
    if any(fragment in message for fragment in transient_fragments):
        return DuckBasinUnavailableError(
            "Quack connection is unavailable"
        )
    return None


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            date_header = response.headers.get("Date")
            current = (
                parsedate_to_datetime(date_header)
                if date_header is not None
                else datetime.now(UTC)
            )
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        return max(0.0, (retry_at - current).total_seconds())


def _copy_duckbasin_error(error: DuckBasinError) -> DuckBasinError:
    if isinstance(error, DuckBasinUnavailableError):
        return type(error)(
            str(error),
            retry_after_seconds=error.retry_after_seconds,
        )
    return type(error)(str(error))
