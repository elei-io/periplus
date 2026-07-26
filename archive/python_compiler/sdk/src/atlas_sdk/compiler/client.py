"""Sync and async clients for the Atlas compiler API."""

from __future__ import annotations

from typing import Literal

import httpx
from pydantic import ValidationError

from atlas_sdk.errors import (
    AtlasAuthenticationError,
    AtlasInvalidRequest,
    AtlasPermissionDenied,
    AtlasProtocolError,
    AtlasServiceUnavailable,
)

from .models import AnalysisResult, CompilationResult


def _raise_http(response: httpx.Response) -> None:
    if response.status_code == 401:
        raise AtlasAuthenticationError("Atlas rejected the supplied credentials.")
    if response.status_code == 403:
        raise AtlasPermissionDenied("Atlas denied access to the compiler.")
    if response.status_code in {400, 409, 422}:
        try:
            payload = response.json()
            detail = payload.get("detail") if isinstance(payload, dict) else None
        except ValueError:
            detail = None
        raise AtlasInvalidRequest(str(detail or "Atlas rejected the request."))
    try:
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AtlasServiceUnavailable(
            "Atlas could not return a trusted compiler response."
        ) from exc


class CompilerClient:
    def __init__(
        self,
        client: httpx.Client,
        *,
        analysis_timeout_seconds: float,
    ) -> None:
        self._client = client
        self._analysis_timeout_seconds = analysis_timeout_seconds

    def compile(self, sql: str) -> CompilationResult:
        try:
            response = self._client.post(
                "catalogue/sql/compile",
                json={"sql": sql, "purpose": "interactive"},
            )
        except httpx.HTTPError as exc:
            raise AtlasServiceUnavailable(
                "Atlas could not return a trusted compiler response."
            ) from exc
        _raise_http(response)
        try:
            return CompilationResult.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise AtlasProtocolError(
                "Atlas returned an invalid compiler response."
            ) from exc

    def analyze(
        self,
        sql: str,
        *,
        per_plan_budget_seconds: float = 60.0,
        execution_policy: Literal["cold", "warm"] = "cold",
        warmup_runs: int = 1,
        equivalence_hashes: bool = False,
    ) -> AnalysisResult:
        try:
            response = self._client.post(
                "catalogue/sql/analyze",
                json={
                    "sql": sql,
                    "purpose": "interactive",
                    "per_plan_budget_seconds": per_plan_budget_seconds,
                    "execution_policy": execution_policy,
                    "warmup_runs": warmup_runs,
                    "equivalence_hashes": equivalence_hashes,
                },
                timeout=self._analysis_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise AtlasServiceUnavailable(
                "Atlas could not analyze the query."
            ) from exc
        _raise_http(response)
        try:
            return AnalysisResult.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise AtlasProtocolError(
                "Atlas returned an invalid analysis response."
            ) from exc


class AsyncCompilerClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        analysis_timeout_seconds: float,
    ) -> None:
        self._client = client
        self._analysis_timeout_seconds = analysis_timeout_seconds

    async def compile(self, sql: str) -> CompilationResult:
        try:
            response = await self._client.post(
                "catalogue/sql/compile",
                json={"sql": sql, "purpose": "interactive"},
            )
        except httpx.HTTPError as exc:
            raise AtlasServiceUnavailable(
                "Atlas could not return a trusted compiler response."
            ) from exc
        _raise_http(response)
        try:
            return CompilationResult.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise AtlasProtocolError(
                "Atlas returned an invalid compiler response."
            ) from exc

    async def analyze(
        self,
        sql: str,
        *,
        per_plan_budget_seconds: float = 60.0,
        execution_policy: Literal["cold", "warm"] = "cold",
        warmup_runs: int = 1,
        equivalence_hashes: bool = False,
    ) -> AnalysisResult:
        try:
            response = await self._client.post(
                "catalogue/sql/analyze",
                json={
                    "sql": sql,
                    "purpose": "interactive",
                    "per_plan_budget_seconds": per_plan_budget_seconds,
                    "execution_policy": execution_policy,
                    "warmup_runs": warmup_runs,
                    "equivalence_hashes": equivalence_hashes,
                },
                timeout=self._analysis_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise AtlasServiceUnavailable(
                "Atlas could not analyze the query."
            ) from exc
        _raise_http(response)
        try:
            return AnalysisResult.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise AtlasProtocolError(
                "Atlas returned an invalid analysis response."
            ) from exc
