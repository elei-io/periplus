"""HTTP clients for the public application's existing /api/query routes."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import math
import os
from typing import TypeVar

import httpx
from pydantic import BaseModel, JsonValue, ValidationError

from .errors import ApiError, ConfigurationError, ResponseError, TransportError
from .types import PreparedQuery, QueryHelpers, QueryResult

Model = TypeVar("Model", bound=BaseModel)


def _options(base_url: str | None, timeout: float) -> dict:
    value = (base_url or os.environ.get("PERIPLUS_PUBLIC_URL", "")).strip()
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL:
        raise ConfigurationError("Provide a valid public application URL.") from None
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.query or url.fragment:
        raise ConfigurationError("Provide an HTTP(S) public application URL without credentials, query or fragment.")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ConfigurationError("timeout must be a positive finite number of seconds.")
    return dict(base_url=str(url).rstrip("/") + "/", timeout=timeout,
                headers={"x-periplus-query-source": "sdk", "accept": "application/json"},
                follow_redirects=False)


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
            if moment.utcoffset() is None:
                return None
            seconds = (moment - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, seconds) if math.isfinite(seconds) else None


def _decode(response: httpx.Response, model: type[Model]) -> Model:
    if not response.is_success:
        code = None
        message = f"Public query request failed (HTTP {response.status_code})."
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = body.get("detail")
                error = detail if isinstance(detail, dict) else body
                code = error.get("code")
                if isinstance(error.get("detail"), str):
                    message = error["detail"]
        except ValueError:
            pass
        raise ApiError(message, status_code=response.status_code,
                       code=code if isinstance(code, str) else None,
                       retry_after_seconds=_retry_after(response.headers.get("retry-after")))
    try:
        return model.model_validate(response.json())
    except (ValueError, ValidationError):
        raise ResponseError("Public query response did not match the expected contract.") from None


def _payload(sql: str, parameters: Sequence[JsonValue] | None) -> dict:
    # Server owns SQL validation, linting, preparation and optimization.
    return {"sql": sql, "parameters": list(parameters) if parameters is not None else []}


class Client:
    """Reusable synchronous public query client. Close it or use a with block."""

    def __init__(self, base_url: str | None = None, *, timeout: float = 140):
        self._http = httpx.Client(**_options(base_url, timeout))

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, model: type[Model], **kwargs) -> Model:
        try:
            response = self._http.request(method, "api/query/" + path, **kwargs)
        except httpx.RequestError:
            raise TransportError("Could not complete the public query request.") from None
        return _decode(response, model)

    def prepare(self, sql: str, parameters: Sequence[JsonValue] | None = None) -> PreparedQuery:
        return self._request("POST", "prep", PreparedQuery, json=_payload(sql, parameters))

    def execute(self, sql: str, parameters: Sequence[JsonValue] | None = None) -> QueryResult:
        return self._request("POST", "exec", QueryResult, json=_payload(sql, parameters))

    def helpers(self) -> QueryHelpers:
        return self._request("GET", "helpers", QueryHelpers)


class AsyncClient:
    """Reusable asynchronous public query client. Use an async with block."""

    def __init__(self, base_url: str | None = None, *, timeout: float = 140):
        self._http = httpx.AsyncClient(**_options(base_url, timeout))

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, model: type[Model], **kwargs) -> Model:
        try:
            response = await self._http.request(method, "api/query/" + path, **kwargs)
        except httpx.RequestError:
            raise TransportError("Could not complete the public query request.") from None
        return _decode(response, model)

    async def prepare(self, sql: str, parameters: Sequence[JsonValue] | None = None) -> PreparedQuery:
        return await self._request("POST", "prep", PreparedQuery, json=_payload(sql, parameters))

    async def execute(self, sql: str, parameters: Sequence[JsonValue] | None = None) -> QueryResult:
        return await self._request("POST", "exec", QueryResult, json=_payload(sql, parameters))

    async def helpers(self) -> QueryHelpers:
        return await self._request("GET", "helpers", QueryHelpers)
