"""Non-throwing product boundary for catalogue SQL compilation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import logging
import time
from typing import Literal

import duckdb

from .compiler import (
    compile_catalogue_query,
    compile_interactive_catalogue_query,
)
from .definition import (
    CatalogueDefinitionDependency,
    analyze_catalogue_definition,
)
from .document_scope import compile_document_scope
from .errors import (
    OptimizationCode,
    OptimizationDiagnostic,
    QueryOptimizationUnavailable,
)
from .metadata import CompilationEstimate
from .lint import (
    CatalogueStatementKind,
    classify_catalogue_statement,
    lint_catalogue_statement,
)
from .physical import add_document_scope_estimate, estimate_compilation
from .syntax import CatalogueQueryError, classify_select
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
class SqlDocumentScopePlan:
    scope_sql: str
    element_columns: tuple[str, ...]
    maximum_documents: int
    maximum_elements: int


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
    document_scope: SqlDocumentScopePlan | None = None

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
    document_scope: SqlDocumentScopePlan | None = None
    normalized_authored: str | None = None
    compilation_sql = sql
    explain_prefix: str | None = None
    interactive_compiled_sql: str | None = None
    if isinstance(purpose, InteractiveQueryPurpose):
        try:
            classified = classify_catalogue_statement(sql)
        except CatalogueQueryError:
            classified = None
        if classified is not None and classified.kind is not CatalogueStatementKind.QUERY:
            compilation_sql = classified.sql
            explain_prefix = (
                "EXPLAIN ANALYZE "
                if classified.kind is CatalogueStatementKind.EXPLAIN_ANALYZE
                else "EXPLAIN "
            )
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
                compilation_sql,
                purpose=purpose,
            )
            interactive_compiled_sql = compilation.sql
            executable_sql = (
                explain_prefix + compilation.sql
                if explain_prefix is not None
                else compilation.sql
            )
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
            normalized_authored = classify_select(compilation_sql).sql(
                dialect="duckdb",
                pretty=True,
            )
        if isinstance(purpose, InteractiveQueryPurpose) and not applied:
            executable_sql = sql
        elif isinstance(purpose, FullMaterializationPurpose):
            if executable_sql != normalized_authored:
                applied = (
                    SqlAppliedRewrite(
                        rule="catalogue_definition_expansion",
                        evidence=(
                            "Authoritative catalogue definitions were expanded "
                            "into the full-refresh query."
                        ),
                    ),
                )
        elif isinstance(purpose, KeyedMaterializationPurpose):
            applied = (
                SqlAppliedRewrite(
                    rule="changed_key_scan_scope",
                    evidence=(
                        "Every proven dependent physical scan was restricted "
                        "to the declared changed-key relation."
                    ),
                ),
            )
        if (
            isinstance(purpose, InteractiveQueryPurpose)
            and purpose.metadata is not None
            and explain_prefix != "EXPLAIN "
        ):
            scope_input_sql = (
                interactive_compiled_sql
                if explain_prefix == "EXPLAIN ANALYZE "
                else executable_sql
            )
            assert scope_input_sql is not None
            scoped = compile_document_scope(
                scope_input_sql,
                metadata=purpose.metadata,
                maximum_documents=purpose.maximum_document_scope,
                maximum_unscoped_element_rows=(
                    purpose.maximum_unscoped_element_rows
                ),
                bound_parameters=purpose.bound_parameters,
            )
            if scoped is not None:
                _validate_generated_sql(scoped.scope_sql)
                executable_sql = (
                    explain_prefix + scoped.sql
                    if explain_prefix is not None
                    else scoped.sql
                )
                document_scope = SqlDocumentScopePlan(
                    scope_sql=scoped.scope_sql,
                    element_columns=scoped.element_columns,
                    maximum_documents=purpose.maximum_document_scope,
                    maximum_elements=purpose.maximum_element_scope,
                )
                applied = (
                    *applied,
                    SqlAppliedRewrite(
                        rule="bounded_document_scan_scope",
                        evidence=(
                            "Managed relationship lineage proves a selective "
                            "document set that is budgeted before elements are read."
                        ),
                    ),
                )
        if not (
            isinstance(purpose, CatalogueDefinitionPurpose)
            and purpose.kind == "scalar_macro"
        ):
            _validate_generated_sql(executable_sql)
        outcome = (
            SqlCompilationOutcome.UNCHANGED
            if isinstance(purpose, CatalogueDefinitionPurpose)
            else (
                SqlCompilationOutcome.OPTIMIZED
                if applied
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
            and explain_prefix is None
            else None
        )
        if estimate is not None and document_scope is not None:
            elements = purpose.metadata.table(
                "elements",
                schema_name="main",
            )
            if elements is not None:
                estimate = add_document_scope_estimate(
                    estimate,
                    elements=elements,
                    maximum_documents=document_scope.maximum_documents,
                    maximum_elements=document_scope.maximum_elements,
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
            document_scope=document_scope,
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
        fallback_allowed = isinstance(
            purpose,
            (
                CatalogueDefinitionPurpose,
                GraphEdgePurpose,
                InteractiveQueryPurpose,
            ),
        )
        if exc.code is OptimizationCode.UNBOUNDED_RELATION:
            fallback_allowed = False
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
            executable_sql=(
                sql if not invalid and fallback_allowed else None
            ),
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


def _validate_generated_sql(sql: str) -> None:
    """Fail closed when the generated DuckDB SQL is not one valid statement."""

    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.ParserException as exc:
        raise QueryOptimizationUnavailable(
            OptimizationDiagnostic(
                code=OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                message=(
                    "Atlas could not render this DuckDB query safely; "
                    "interactive execution should use the authored SQL."
                ),
                documentation_anchor="query-boundary",
            )
        ) from exc
    if len(statements) != 1:
        raise QueryOptimizationUnavailable(
            OptimizationDiagnostic(
                code=OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                message="Generated SQL must contain exactly one statement.",
                documentation_anchor="query-boundary",
            )
        )


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
    if code in {
        "absurd_limit",
        "missing_limit",
        "unbounded_dom_helper",
    }:
        return "performance_advisory"
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
        OptimizationCode.RESERVED_RELATION.value,
        OptimizationCode.SOURCE_NOT_READ.value,
        OptimizationCode.UNSUPPORTED_PROJECTION.value,
        OptimizationCode.UNSUPPORTED_QUERY_SHAPE.value,
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
