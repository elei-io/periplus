"""Public embedded and remote Atlas compiler facade."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from hashlib import sha256
import time
from typing import Protocol

import httpx

from catalogue.compiler import (
    CatalogueDefinitionPurpose,
    FullMaterializationPurpose,
    GraphEdgePurpose,
    InteractiveQueryPurpose,
    KeyedMaterializationPurpose,
    compile_catalogue_sql,
)

from .models import (
    AnalysisResult,
    CompilationResult,
    compilation_result_from_internal,
)


try:
    COMPILER_VERSION = version("atlas")
except PackageNotFoundError:
    COMPILER_VERSION = "0.1.0"


CompilationPurpose = (
    CatalogueDefinitionPurpose
    | FullMaterializationPurpose
    | GraphEdgePurpose
    | InteractiveQueryPurpose
    | KeyedMaterializationPurpose
)


class AtlasCompilerUnavailable(RuntimeError):
    """The remote compiler service could not return a trusted decision."""


class _CompilerBackend(Protocol):
    def compile(
        self,
        sql: str,
        *,
        purpose: CompilationPurpose,
        coverage_source: str | None,
    ) -> CompilationResult: ...


class AtlasCompiler:
    """Compile DuckDB SQL without coupling callers to compiler internals."""

    def __init__(self, backend: _CompilerBackend) -> None:
        self._backend = backend

    @classmethod
    def embedded(
        cls,
        *,
        catalogue_revision: str | None = None,
    ) -> "AtlasCompiler":
        return cls(
            _EmbeddedCompilerBackend(
                catalogue_revision=catalogue_revision,
            )
        )

    @classmethod
    def remote(
        cls,
        url: str,
        *,
        token: str | None = None,
        timeout_seconds: float = 10.0,
        analysis_timeout_seconds: float = 120.0,
    ) -> "AtlasCompiler":
        return cls(
            _RemoteCompilerBackend(
                url=url,
                token=token,
                timeout_seconds=timeout_seconds,
                analysis_timeout_seconds=analysis_timeout_seconds,
            )
        )

    def compile(
        self,
        sql: str,
        *,
        purpose: CompilationPurpose | None = None,
        coverage_source: str | None = None,
    ) -> CompilationResult:
        return self._backend.compile(
            sql,
            purpose=purpose or InteractiveQueryPurpose(),
            coverage_source=coverage_source,
        )

    def analyze(
        self,
        sql: str,
        *,
        per_plan_budget_seconds: float = 60.0,
        execution_policy: str = "cold",
        warmup_runs: int = 1,
        equivalence_hashes: bool = False,
    ) -> AnalysisResult:
        """Compare authored and compiled plans against a remote Atlas lake."""

        analyze = getattr(self._backend, "analyze", None)
        if analyze is None:
            raise AtlasCompilerUnavailable(
                "Lake analysis requires a remote Atlas compiler."
            )
        return analyze(
            sql,
            per_plan_budget_seconds=per_plan_budget_seconds,
            execution_policy=execution_policy,
            warmup_runs=warmup_runs,
            equivalence_hashes=equivalence_hashes,
        )

    def close(self) -> None:
        close = getattr(self._backend, "close", None)
        if close is not None:
            close()

    def __enter__(self) -> "AtlasCompiler":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _EmbeddedCompilerBackend:
    def __init__(self, *, catalogue_revision: str | None) -> None:
        self.catalogue_revision = catalogue_revision

    def compile(
        self,
        sql: str,
        *,
        purpose: CompilationPurpose,
        coverage_source: str | None,
    ) -> CompilationResult:
        started = time.perf_counter()
        snapshot = getattr(purpose, "metadata", None)
        catalogue_revision = (
            snapshot.revision
            if snapshot is not None
            else self.catalogue_revision
        )
        if (
            snapshot is not None
            and self.catalogue_revision is not None
            and self.catalogue_revision != snapshot.revision
        ):
            raise ValueError(
                "The compiler backend revision does not match the supplied "
                "catalogue metadata snapshot."
            )
        internal = compile_catalogue_sql(
                sql,
                purpose=purpose,
                coverage_source=coverage_source,
                compiler_version=COMPILER_VERSION,
                catalogue_revision=catalogue_revision,
            )
        result = compilation_result_from_internal(
            internal,
            catalogue_revision=catalogue_revision,
            compiler_version=COMPILER_VERSION,
        )
        categories = {
            _public_diagnostic_category(item.code)
            for item in result.diagnostics
        }
        return result.model_copy(
            update={
                "compilation_latency_seconds": time.perf_counter() - started,
                "fingerprint": sha256(sql.encode()).hexdigest()[:16],
                "diagnostic_category": ",".join(sorted(categories)) or "none",
            }
        )


class _RemoteCompilerBackend:
    def __init__(
        self,
        *,
        url: str,
        token: str | None,
        timeout_seconds: float,
        analysis_timeout_seconds: float,
    ) -> None:
        headers = (
            {"Authorization": f"Bearer {token}"}
            if token is not None
            else None
        )
        self._client = httpx.Client(
            base_url=url.rstrip("/") + "/",
            headers=headers,
            timeout=timeout_seconds,
        )
        self._analysis_timeout_seconds = analysis_timeout_seconds

    def compile(
        self,
        sql: str,
        *,
        purpose: CompilationPurpose,
        coverage_source: str | None,
    ) -> CompilationResult:
        del coverage_source
        if not isinstance(purpose, InteractiveQueryPurpose):
            raise ValueError(
                "The remote Atlas compiler currently supports interactive SQL only."
            )
        try:
            response = self._client.post(
                "catalogue/sql/compile",
                json={"sql": sql, "purpose": "interactive"},
            )
            response.raise_for_status()
            return CompilationResult.model_validate(response.json())
        except (
            httpx.HTTPError,
            ValueError,
        ) as exc:
            raise AtlasCompilerUnavailable(
                "The Atlas compiler service could not return a trusted result."
            ) from exc

    def analyze(
        self,
        sql: str,
        *,
        per_plan_budget_seconds: float,
        execution_policy: str,
        warmup_runs: int,
        equivalence_hashes: bool,
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
            response.raise_for_status()
            return AnalysisResult.model_validate(response.json())
        except (
            httpx.HTTPError,
            ValueError,
        ) as exc:
            raise AtlasCompilerUnavailable(
                "The Atlas compiler service could not analyze the query."
            ) from exc

    def close(self) -> None:
        self._client.close()


def _public_diagnostic_category(code: str) -> str:
    if code in {
        "absurd_limit",
        "missing_limit",
        "unbounded_dom_helper",
    }:
        return "performance_advisory"
    if code == "invalid_query" or "syntax" in code:
        return "unsupported_syntax"
    if code in {"unsupported_function", "unsupported_relation"}:
        return "unavailable_metadata"
    if code in {
        "nondeterministic_function",
        "unbounded_relation",
        "key_not_preserved",
        "key_required",
        "reserved_relation",
        "source_not_read",
        "unsupported_projection",
        "unsupported_query_shape",
    }:
        return "unsafe_semantics"
    if code in {"unmanaged_relation", "unsupported_purpose"}:
        return "upstream_limitation"
    return "unsupported_syntax"
