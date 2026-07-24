"""Non-throwing product boundary for catalogue SQL compilation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import logging
import time
from typing import Literal

from .compiler import (
    compile_catalogue_query,
    compile_interactive_catalogue_query,
)
from .definition import (
    CatalogueDefinitionDependency,
    analyze_catalogue_definition,
)
from .errors import (
    OptimizationCode,
    QueryOptimizationUnavailable,
)
from .metadata import CompilationEstimate
from .lint import lint_catalogue_statement
from .physical import estimate_compilation
from .syntax import classify_select
from .graph_edge import (
    GraphEdgeCompilationError,
    compile_graph_edge_query,
)
from .purpose import (
    CatalogueDefinitionPurpose,
    FullMaterializationPurpose,
    GraphEdgePurpose,
    InteractiveQueryPurpose,
    KeyedMaterializationPurpose,
)

_logger = logging.getLogger(__name__)


class SqlCompilationOutcome(StrEnum):
    INVALID = "invalid"
    OPTIMIZED = "optimized"
    UNCHANGED = "unchanged"
    UNSUPPORTED = "unsupported"


class SqlCompilationPurpose(StrEnum):
    CATALOGUE_DEFINITION = "catalogue_definition"
    GRAPH_EDGE = "graph_edge"
    INTERACTIVE = "interactive"
    FULL_MATERIALIZATION = "full_materialization"
    KEYED_MATERIALIZATION = "keyed_materialization"


@dataclass(frozen=True, slots=True)
class SqlCompilationDiagnostic:
    code: str
    severity: Literal["warning", "error"]
    message: str
    sql_fragment: str | None = None
    documentation_anchor: str | None = None


@dataclass(frozen=True, slots=True)
class SqlAppliedRewrite:
    rule: str
    evidence: str


@dataclass(frozen=True, slots=True)
class SqlCompilationResult:
    purpose: SqlCompilationPurpose
    outcome: SqlCompilationOutcome
    authored_sql: str
    executable_sql: str | None
    diagnostics: tuple[SqlCompilationDiagnostic, ...]
    applied_rewrites: tuple[SqlAppliedRewrite, ...] = ()
    dependencies: tuple[CatalogueDefinitionDependency, ...] = ()
    estimate: CompilationEstimate | None = None

    @property
    def valid(self) -> bool:
        return self.outcome is not SqlCompilationOutcome.INVALID

    @property
    def supported(self) -> bool:
        return self.outcome not in {
            SqlCompilationOutcome.INVALID,
            SqlCompilationOutcome.UNSUPPORTED,
        }

    @property
    def materialization_eligible(self) -> bool:
        return (
            self.purpose
            in {
                SqlCompilationPurpose.FULL_MATERIALIZATION,
                SqlCompilationPurpose.KEYED_MATERIALIZATION,
            }
            and self.supported
        )


CompilationPurpose = (
    CatalogueDefinitionPurpose
    | FullMaterializationPurpose
    | GraphEdgePurpose
    | InteractiveQueryPurpose
    | KeyedMaterializationPurpose
)


def compile_catalogue_sql(
    sql: str,
    *,
    purpose: CompilationPurpose,
    coverage_source: str | None = None,
    compiler_version: str = "internal",
    catalogue_revision: str | None = None,
) -> SqlCompilationResult:
    """Return one authoritative SQL decision for expected user input outcomes."""

    purpose_name = _purpose_name(purpose)
    started = time.perf_counter()
    diagnostics = [
        SqlCompilationDiagnostic(
            code=item.code,
            severity="warning",
            message=item.message,
        )
        for item in (
            lint_catalogue_statement(sql)
            if isinstance(purpose, InteractiveQueryPurpose)
            else ()
        )
    ]
    applied: tuple[SqlAppliedRewrite, ...] = ()
    dependencies: tuple[CatalogueDefinitionDependency, ...] = ()
    normalized_authored: str | None = None
    try:
        if isinstance(purpose, CatalogueDefinitionPurpose):
            definition = analyze_catalogue_definition(sql, purpose=purpose)
            executable_sql = sql
            dependencies = definition.dependencies
            normalized_authored = definition.normalized_sql
        elif isinstance(purpose, GraphEdgePurpose):
            compilation = compile_graph_edge_query(sql, purpose=purpose)
            executable_sql = compilation.sql
            applied = tuple(
                SqlAppliedRewrite(
                    rule=rewrite.rule.value,
                    evidence=rewrite.evidence,
                )
                for rewrite in compilation.applied_rewrites
            )
        elif isinstance(purpose, InteractiveQueryPurpose):
            compilation = compile_interactive_catalogue_query(
                sql,
                purpose=purpose,
            )
            executable_sql = compilation.sql
            applied = tuple(
                SqlAppliedRewrite(
                    rule=rewrite.rule.value,
                    evidence=rewrite.evidence,
                )
                for rewrite in compilation.applied_rewrites
            )
            if any(
                rewrite.rule == "bounded_scalar_input"
                for rewrite in applied
            ):
                diagnostics = [
                    diagnostic
                    for diagnostic in diagnostics
                    if diagnostic.code != "unbounded_dom_helper"
                ]
        else:
            executable_sql = compile_catalogue_query(
                sql,
                purpose=purpose,
            )
        if normalized_authored is None:
            normalized_authored = classify_select(sql).sql(
                dialect="duckdb",
                pretty=True,
            )
        outcome = (
            SqlCompilationOutcome.UNCHANGED
            if isinstance(purpose, CatalogueDefinitionPurpose)
            else (
                SqlCompilationOutcome.OPTIMIZED
                if applied or executable_sql != normalized_authored
                else SqlCompilationOutcome.UNCHANGED
            )
        )
        estimate = (
            estimate_compilation(
                sql,
                executable_sql,
                metadata=purpose.metadata,
                bound_parameters=purpose.bound_parameters,
            )
            if isinstance(purpose, InteractiveQueryPurpose)
            and purpose.metadata is not None
            else None
        )
        result = SqlCompilationResult(
            purpose=purpose_name,
            outcome=outcome,
            authored_sql=sql,
            executable_sql=executable_sql,
            diagnostics=tuple(diagnostics),
            applied_rewrites=applied,
            dependencies=dependencies,
            estimate=estimate,
        )
    except GraphEdgeCompilationError as exc:
        diagnostics.append(
            SqlCompilationDiagnostic(
                code=exc.diagnostic.code,
                severity="error",
                message=exc.diagnostic.message,
                sql_fragment=exc.diagnostic.sql_fragment,
                documentation_anchor=exc.diagnostic.documentation_anchor,
            )
        )
        result = SqlCompilationResult(
            purpose=purpose_name,
            outcome=SqlCompilationOutcome.INVALID,
            authored_sql=sql,
            executable_sql=None,
            diagnostics=tuple(diagnostics),
        )
    except QueryOptimizationUnavailable as exc:
        invalid = exc.code is OptimizationCode.INVALID_QUERY
        diagnostics.append(
            SqlCompilationDiagnostic(
                code=exc.code.value,
                severity="error" if invalid else "warning",
                message=exc.diagnostic.message,
                sql_fragment=exc.diagnostic.sql_fragment,
                documentation_anchor=(
                    exc.diagnostic.documentation_anchor
                ),
            )
        )
        result = SqlCompilationResult(
            purpose=purpose_name,
            outcome=(
                SqlCompilationOutcome.INVALID
                if invalid
                else SqlCompilationOutcome.UNSUPPORTED
            ),
            authored_sql=sql,
            executable_sql=None if invalid else sql,
            diagnostics=tuple(diagnostics),
        )
    except Exception as exc:
        from observability import compiler_metrics

        compiler_metrics.defect(
            source=_bounded_source(coverage_source or "unspecified"),
            purpose=purpose_name.value,
            exception_type=type(exc).__name__,
        )
        _logger.error(
            "catalogue SQL compiler defect source=%s purpose=%s "
            "compiler_version=%s catalogue_revision=%s exception_type=%s",
            coverage_source or "unspecified",
            purpose_name.value,
            compiler_version,
            catalogue_revision or "-",
            type(exc).__name__,
        )
        raise
    _record_coverage(
        result,
        source=coverage_source or "unspecified",
        compiler_version=compiler_version,
        catalogue_revision=catalogue_revision,
        duration_seconds=time.perf_counter() - started,
    )
    return result


def _purpose_name(purpose: CompilationPurpose) -> SqlCompilationPurpose:
    if isinstance(purpose, CatalogueDefinitionPurpose):
        return SqlCompilationPurpose.CATALOGUE_DEFINITION
    if isinstance(purpose, InteractiveQueryPurpose):
        return SqlCompilationPurpose.INTERACTIVE
    if isinstance(purpose, GraphEdgePurpose):
        return SqlCompilationPurpose.GRAPH_EDGE
    if isinstance(purpose, FullMaterializationPurpose):
        return SqlCompilationPurpose.FULL_MATERIALIZATION
    return SqlCompilationPurpose.KEYED_MATERIALIZATION


def _record_coverage(
    result: SqlCompilationResult,
    *,
    source: str,
    compiler_version: str,
    catalogue_revision: str | None,
    duration_seconds: float,
) -> None:
    from observability import compiler_metrics

    source = _bounded_source(source)
    fingerprint = sha256(result.authored_sql.encode()).hexdigest()[:16]
    classified = tuple(
        (_diagnostic_category(diagnostic.code), diagnostic.code)
        for diagnostic in result.diagnostics
    )
    categories = sorted({category for category, _ in classified})
    category = ",".join(categories) or "none"
    codes = ",".join(code for _, code in classified) or "-"
    rules = tuple(rewrite.rule for rewrite in result.applied_rewrites)
    compiler_metrics.completed(
        source=source,
        purpose=result.purpose.value,
        outcome=result.outcome.value,
        diagnostic_category=category,
        compiler_version=compiler_version,
        duration_seconds=duration_seconds,
        diagnostics=classified,
        rewrite_rules=rules,
    )
    _logger.info(
        "catalogue SQL coverage source=%s purpose=%s outcome=%s "
        "category=%s codes=%s rules=%s compiler_version=%s "
        "catalogue_revision=%s latency_seconds=%.6f fingerprint=%s",
        source,
        result.purpose.value,
        result.outcome.value,
        category,
        codes,
        ",".join(rules) or "-",
        compiler_version,
        catalogue_revision or "-",
        duration_seconds,
        fingerprint,
    )


def _diagnostic_category(code: str) -> str:
    if code == OptimizationCode.INVALID_QUERY.value or "syntax" in code:
        return "unsupported_syntax"
    if code in {
        OptimizationCode.UNSUPPORTED_FUNCTION.value,
        OptimizationCode.UNSUPPORTED_RELATION.value,
    }:
        return "unavailable_metadata"
    if code in {
        OptimizationCode.NONDETERMINISTIC_FUNCTION.value,
        OptimizationCode.UNBOUNDED_RELATION.value,
        OptimizationCode.KEY_NOT_PRESERVED.value,
        OptimizationCode.KEY_REQUIRED.value,
    }:
        return "unsafe_semantics"
    if code in {
        OptimizationCode.UNMANAGED_RELATION.value,
        OptimizationCode.UNSUPPORTED_PURPOSE.value,
    }:
        return "upstream_limitation"
    return "unsupported_syntax"


def _bounded_source(source: str) -> str:
    return (
        source
        if source
        in {
            "definition_authoring",
            "graph_edge",
            "interactive_execution",
            "materialization_execution",
            "saved_query_authoring",
            "unspecified",
        }
        else "other"
    )
