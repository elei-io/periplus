"""Wire models for Atlas SQL compilation and analysis."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class CompilationOutcome(StrEnum):
    INVALID = "invalid"
    OPTIMIZED = "optimized"
    UNCHANGED = "unchanged"
    UNSUPPORTED = "unsupported"


class CompilationPurposeName(StrEnum):
    CATALOGUE_DEFINITION = "catalogue_definition"
    GRAPH_EDGE = "graph_edge"
    INTERACTIVE = "interactive"
    FULL_MATERIALIZATION = "full_materialization"
    KEYED_MATERIALIZATION = "keyed_materialization"


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class CompilationDiagnostic(_WireModel):
    code: str
    severity: Literal["warning", "error"]
    message: str
    sql_fragment: str | None = None
    documentation_anchor: str | None = None


class AppliedRewrite(_WireModel):
    rule: str
    evidence: str


class DefinitionDependency(_WireModel):
    kind: str
    qualified_name: str
    path: tuple[str, ...]


class ScanEstimate(_WireModel):
    relation: str
    table_uuid: str
    estimated_rows_read: int | None
    estimated_bytes_read: int | None
    partition_predicates: tuple[str, ...] = ()


class CompilationEstimate(_WireModel):
    authored_scans: tuple[ScanEstimate, ...]
    executable_scans: tuple[ScanEstimate, ...]
    estimated_rows_avoided: int | None
    estimated_bytes_avoided: int | None


class DocumentScopePlan(_WireModel):
    scope_sql: str
    element_columns: tuple[str, ...]
    maximum_documents: int
    maximum_elements: int


class CompilationResult(_WireModel):
    protocol_version: Literal[1] = 1
    purpose: CompilationPurposeName
    outcome: CompilationOutcome
    authored_sql: str
    executable_sql: str | None
    diagnostics: tuple[CompilationDiagnostic, ...]
    applied_rewrites: tuple[AppliedRewrite, ...] = ()
    dependencies: tuple[DefinitionDependency, ...] = ()
    estimate: CompilationEstimate | None = None
    document_scope: DocumentScopePlan | None = None
    catalogue_revision: str | None = None
    compiler_version: str
    valid: bool
    supported: bool
    materialization_eligible: bool
    compilation_latency_seconds: float | None = None
    fingerprint: str | None = None
    diagnostic_category: str = "none"


class AnalysisOperator(_WireModel):
    path: str
    name: str
    operator_type: str | None = None
    seconds: float | None = None
    rows_returned: int | None = None
    rows_scanned: int | None = None


class AnalysisMetrics(_WireModel):
    latency_seconds: float | None = None
    cpu_seconds: float | None = None
    total_bytes_read: int | None = None
    peak_buffer_memory_bytes: int | None = None
    cumulative_rows_scanned: int | None = None
    cumulative_cardinality: int | None = None
    operators: tuple[AnalysisOperator, ...] = ()


class AnalysisPlanStatus(StrEnum):
    SUCCEEDED = "succeeded"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    MEMORY_EXHAUSTED = "memory_exhausted"
    FAILED = "failed"


class AnalysisPlanOutcome(_WireModel):
    status: AnalysisPlanStatus
    metrics: AnalysisMetrics | None = None
    error_category: str | None = None
    error: str | None = None
    attempts: int = 1
    equivalence_hash: str | None = None


class AnalysisOperatorDifference(_WireModel):
    path: str
    authored_name: str | None = None
    compiled_name: str | None = None
    seconds_delta: float | None = None
    rows_scanned_delta: int | None = None


class AnalysisComparison(_WireModel):
    latency_speedup: float | None = None
    cpu_speedup: float | None = None
    rows_scanned_reduction: float | None = None
    bytes_read_reduction: float | None = None
    peak_buffer_memory_reduction: float | None = None
    operator_differences: tuple[AnalysisOperatorDifference, ...] = ()
    equivalence_hashes_match: bool | None = None


class AnalysisResult(_WireModel):
    protocol_version: Literal[1] = 1
    compilation: CompilationResult
    authored: AnalysisPlanOutcome
    compiled: AnalysisPlanOutcome
    comparison: AnalysisComparison | None = None
    execution_policy: Literal["cold", "warm"] = "cold"
    per_plan_budget_seconds: float
