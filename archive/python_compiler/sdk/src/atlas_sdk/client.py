"""Top-level sync and async Atlas clients."""

from __future__ import annotations

import httpx

from .compiler import AsyncCompilerClient, CompilerClient


def _headers(token: str | None) -> dict[str, str] | None:
    return {"Authorization": f"Bearer {token}"} if token is not None else None


class AtlasClient:
    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        timeout_seconds: float = 10.0,
        analysis_timeout_seconds: float = 120.0,
    ) -> None:
        self._http = httpx.Client(
            base_url=url.rstrip("/") + "/",
            headers=_headers(token),
            timeout=timeout_seconds,
        )
        self.compiler = CompilerClient(
            self._http,
            analysis_timeout_seconds=analysis_timeout_seconds,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "AtlasClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class AsyncAtlasClient:
    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        timeout_seconds: float = 10.0,
        analysis_timeout_seconds: float = 120.0,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=url.rstrip("/") + "/",
            headers=_headers(token),
            timeout=timeout_seconds,
        )
        self.compiler = AsyncCompilerClient(
            self._http,
            analysis_timeout_seconds=analysis_timeout_seconds,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncAtlasClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()
