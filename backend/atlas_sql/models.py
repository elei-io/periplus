"""Public SDK wire models and internal conversion at the Atlas boundary."""

from atlas_sdk.compiler.models import (
    AnalysisComparison,
    AnalysisMetrics,
    AnalysisOperator,
    AnalysisOperatorDifference,
    AnalysisPlanOutcome,
    AnalysisPlanStatus,
    AnalysisResult,
    AppliedRewrite,
    CompilationDiagnostic,
    CompilationEstimate,
    CompilationOutcome,
    CompilationPurposeName,
    CompilationResult,
    DefinitionDependency,
    ScanEstimate,
)
from catalogue.compiler import SqlCompilationResult


def compilation_result_from_internal(
    result: SqlCompilationResult,
    *,
    catalogue_revision: str | None,
    compiler_version: str,
) -> CompilationResult:
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

    estimate = result.estimate
    return CompilationResult(
        purpose=CompilationPurposeName(result.purpose.value),
        outcome=CompilationOutcome(result.outcome.value),
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
            CompilationEstimate(
                authored_scans=scans(estimate.authored_scans),
                executable_scans=scans(estimate.executable_scans),
                estimated_rows_avoided=estimate.estimated_rows_avoided,
                estimated_bytes_avoided=estimate.estimated_bytes_avoided,
            )
            if estimate is not None
            else None
        ),
        catalogue_revision=catalogue_revision,
        compiler_version=compiler_version,
        valid=result.valid,
        supported=result.supported,
        materialization_eligible=result.materialization_eligible,
    )


__all__ = [
    "AnalysisComparison",
    "AnalysisMetrics",
    "AnalysisOperator",
    "AnalysisOperatorDifference",
    "AnalysisPlanOutcome",
    "AnalysisPlanStatus",
    "AnalysisResult",
    "AppliedRewrite",
    "CompilationDiagnostic",
    "CompilationEstimate",
    "CompilationOutcome",
    "CompilationPurposeName",
    "CompilationResult",
    "DefinitionDependency",
    "ScanEstimate",
    "compilation_result_from_internal",
]
