"""Read authoritative execution limits without composing control database credentials."""
import asyncio
import os

import httpx
from pydantic import ValidationError

from periplus.operations.access.schemas import QueryLimits


class QueryLimitsUnavailable(Exception):
    pass


class QueryLimitsClient:
    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=os.environ.get("PERIPLUS_API_URL", "http://127.0.0.1:8000").rstrip("/") + "/",
            headers={"Authorization": "Bearer " + os.environ.get("PERIPLUS_QUERY_API_TOKEN", "")},
            timeout=5, limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            follow_redirects=False, trust_env=False,
        )

    async def close(self):
        await self.client.aclose()

    async def read(self) -> QueryLimits:
        try:
            async with asyncio.timeout(5):
                response = await self.client.get("access")
            response.raise_for_status()
            sql = response.json()["sql"]
            return QueryLimits(max_rows=sql["max_rows"], max_duration_seconds=sql["max_duration_seconds"],
                               max_result_bytes=sql["max_result_bytes"])
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, ValidationError):
            # No cached/default policy on failure; no origin details reach the caller.
            raise QueryLimitsUnavailable() from None
