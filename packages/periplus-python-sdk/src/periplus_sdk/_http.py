"""Small async HTTP boundary used by collection and control resources."""

from __future__ import annotations

from typing import Any
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import math

import httpx

from ._config import api_config
from .errors import (
    ApiError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)


async def request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    config = api_config()
    headers = (
        {"Authorization": f"Bearer {config.api_token}"}
        if config.api_token
        else {}
    )
    async with httpx.AsyncClient(
        base_url=f"{config.api_url}/",
        headers=headers,
        timeout=30,
    ) as client:
        response = await client.request(
            method,
            path.lstrip("/"),
            json=json,
            params=params,
        )
    if response.is_error:
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        message = (
            detail
            if isinstance(detail, str)
            else f"Periplus API request failed (HTTP {response.status_code})"
        )
        error_type: type[ApiError]
        if response.status_code in {401, 403}:
            raise AuthenticationError(message)
        if response.status_code == 404:
            error_type = NotFoundError
        elif response.status_code == 409:
            error_type = ConflictError
        elif response.status_code == 422:
            error_type = ValidationError
        else:
            error_type = ApiError
        raise error_type(message, status_code=response.status_code,
                         retry_after_seconds=_retry_after(response.headers.get("Retry-After")))
    if response.status_code == 204:
        return None
    return response.json()


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
