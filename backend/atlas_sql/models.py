"""Stable public result models for Atlas SQL compilation."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from catalogue.compiler import (
    CompilationEstimate as InternalCompilationEstimate,
    SqlCompilationOutcome,
    SqlCompilationPurpose,
    SqlCompilationResult,
)


class CompilationDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    severity: Literal["warning", "error"]
    message: str
    sql_fragment: str | None = None
    documentation_anchor: str | None = None


class AppliedRewrite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: str
    evidence: str


class DefinitionDependency(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    qualified_name: str
    path: tuple[str, ...]


class ScanEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relation: str
    table_uuid: str
    estimated_rows_read: int | None
    estimated_bytes_read: int | None
    partition_predicates: tuple[str, ...] = ()


class CompilationEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authored_scans: tuple[ScanEstimate, ...]
    executable_scans: tuple[ScanEstimate, ...]
    estimated_rows_avoided: int | None
    estimated_bytes_avoided: int | None

    @classmethod
    def from_internal(
        cls,
        estimate: InternalCompilationEstimate,
    ) -> "CompilationEstimate":
        def scans(items) -> tuple[ScanEstimate, ...]:
            return tuple(
                ScanEstimate(
                    relation=item.relation,
                    table_uuid=item.table_uuid,
                    estimated_rows_read=item.estimated_rows_read,
                    estimated_bytes_read=item.estimated_bytes_read,
                    partition_predicates=item.partition_predicates,
                )
                for item in items
            )

        return cls(
            authored_scans=scans(estimate.authored_scans),
            executable_scans=scans(estimate.executable_scans),
            estimated_rows_avoided=estimate.estimated_rows_avoided,
            estimated_bytes_avoided=estimate.estimated_bytes_avoided,
        )


class CompilationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: SqlCompilationPurpose
    outcome: SqlCompilationOutcome
    authored_sql: str
    executable_sql: str | None
    diagnostics: tuple[CompilationDiagnostic, ...]
    applied_rewrites: tuple[AppliedRewrite, ...] = ()
    dependencies: tuple[DefinitionDependency, ...] = ()
    estimate: CompilationEstimate | None = None
    catalogue_revision: str | None = None
    compiler_version: str
    valid: bool
    supported: bool
    materialization_eligible: bool
    compilation_latency_seconds: float | None = None
    fingerprint: str | None = None
    diagnostic_category: str = "none"

    @classmethod
    def from_internal(
        cls,
        result: SqlCompilationResult,
        *,
        catalogue_revision: str | None,
        compiler_version: str,
    ) -> "CompilationResult":
        return cls(
            purpose=result.purpose,
            outcome=result.outcome,
            authored_sql=result.authored_sql,
            executable_sql=result.executable_sql,
            diagnostics=tuple(
                CompilationDiagnostic(
                    code=item.code,
                    severity=item.severity,
                    message=item.message,
                    sql_fragment=item.sql_fragment,
                    documentation_anchor=item.documentation_anchor,
                )
                for item in result.diagnostics
            ),
            applied_rewrites=tuple(
                AppliedRewrite(rule=item.rule, evidence=item.evidence)
                for item in result.applied_rewrites
            ),
            dependencies=tuple(
                DefinitionDependency(
                    kind=item.kind,
                    qualified_name=item.qualified_name,
                    path=item.path,
                )
                for item in result.dependencies
            ),
            estimate=(
                CompilationEstimate.from_internal(result.estimate)
                if result.estimate is not None
                else None
            ),
            catalogue_revision=catalogue_revision,
            compiler_version=compiler_version,
            valid=result.valid,
            supported=result.supported,
            materialization_eligible=result.materialization_eligible,
        )


class AnalysisMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    latency_seconds: float | None = None
    cpu_seconds: float | None = None
    total_bytes_read: int | None = None
    peak_buffer_memory_bytes: int | None = None
    cumulative_rows_scanned: int | None = None
    cumulative_cardinality: int | None = None
    operators: tuple["AnalysisOperator", ...] = ()


class AnalysisOperator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    name: str
    operator_type: str | None = None
    seconds: float | None = None
    rows_returned: int | None = None
    rows_scanned: int | None = None


class AnalysisPlanStatus(StrEnum):
    SUCCEEDED = "succeeded"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    MEMORY_EXHAUSTED = "memory_exhausted"
    FAILED = "failed"


class AnalysisPlanOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: AnalysisPlanStatus
    metrics: AnalysisMetrics | None = None
    error_category: str | None = None
    error: str | None = None
    attempts: int = 1
    equivalence_hash: str | None = None


class AnalysisOperatorDifference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    authored_name: str | None = None
    compiled_name: str | None = None
    seconds_delta: float | None = None
    rows_scanned_delta: int | None = None


class AnalysisComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    latency_speedup: float | None = None
    cpu_speedup: float | None = None
    rows_scanned_reduction: float | None = None
    bytes_read_reduction: float | None = None
    peak_buffer_memory_reduction: float | None = None
    operator_differences: tuple[AnalysisOperatorDifference, ...] = ()
    equivalence_hashes_match: bool | None = None


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    compilation: CompilationResult
    authored: AnalysisPlanOutcome
    compiled: AnalysisPlanOutcome
    comparison: AnalysisComparison | None = None
    execution_policy: Literal["cold", "warm"] = "cold"
    per_plan_budget_seconds: float
