"""Comparative compiler profiling through the bounded interactive runtime."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import json
from typing import Any, Literal

from atlas_sql import (
    AnalysisComparison,
    AnalysisMetrics,
    AnalysisOperator,
    AnalysisOperatorDifference,
    AnalysisPlanOutcome,
    AnalysisPlanStatus,
    AnalysisResult,
    CompilationOutcome,
    CompilationResult,
)
from repository.catalogue.interactive import (
    BufferedCatalogueResult,
    execute_interactive_query,
)
from repository.catalogue.quack_runtime import (
    CatalogueQueryExecutionError,
    QuackQueryRuntime,
)


async def analyze_compilation(
    runtime: QuackQueryRuntime,
    compilation: CompilationResult,
    *,
    per_plan_budget_seconds: float = 60.0,
    execution_policy: Literal["cold", "warm"] = "cold",
    warmup_runs: int = 1,
    equivalence_hashes: bool = False,
) -> AnalysisResult:
    """Profile each plan independently and retain partial failure results."""

    if per_plan_budget_seconds <= 0:
        raise ValueError("per_plan_budget_seconds must be greater than zero")
    if warmup_runs < 0 or warmup_runs > 10:
        raise ValueError("warmup_runs must be between zero and ten")
    if not compilation.valid or compilation.executable_sql is None:
        raise CatalogueQueryExecutionError(
            compilation.diagnostics[-1].message
            if compilation.diagnostics
            else "The SQL is not valid."
        )

    authored_compilation = compilation.model_copy(
        update={
            "outcome": CompilationOutcome.UNCHANGED,
            "executable_sql": compilation.authored_sql,
            "applied_rewrites": (),
        }
    )
    authored = await _run_plan(
        runtime,
        authored_compilation,
        budget_seconds=per_plan_budget_seconds,
        execution_policy=execution_policy,
        warmup_runs=warmup_runs,
        equivalence_hashes=equivalence_hashes,
    )
    if compilation.executable_sql == compilation.authored_sql:
        compiled = authored
    else:
        compiled = await _run_plan(
            runtime,
            compilation,
            budget_seconds=per_plan_budget_seconds,
            execution_policy=execution_policy,
            warmup_runs=warmup_runs,
            equivalence_hashes=equivalence_hashes,
        )
    comparison = (
        _compare(authored, compiled)
        if authored.metrics is not None and compiled.metrics is not None
        else None
    )
    return AnalysisResult(
        compilation=compilation,
        authored=authored,
        compiled=compiled,
        comparison=comparison,
        execution_policy=execution_policy,
        per_plan_budget_seconds=per_plan_budget_seconds,
    )


async def _run_plan(
    runtime: QuackQueryRuntime,
    compilation: CompilationResult,
    *,
    budget_seconds: float,
    execution_policy: Literal["cold", "warm"],
    warmup_runs: int,
    equivalence_hashes: bool,
) -> AnalysisPlanOutcome:
    attempts = 1
    try:
        async with asyncio.timeout(budget_seconds):
            if execution_policy == "warm":
                for _ in range(warmup_runs):
                    await _profile(runtime, compilation)
                    attempts += 1
            metrics = await _profile(runtime, compilation)
            result_hash = (
                await _equivalence_hash(runtime, compilation)
                if equivalence_hashes
                else None
            )
        return AnalysisPlanOutcome(
            status=AnalysisPlanStatus.SUCCEEDED,
            metrics=metrics,
            attempts=attempts,
            equivalence_hash=result_hash,
        )
    except TimeoutError:
        return AnalysisPlanOutcome(
            status=AnalysisPlanStatus.TIMED_OUT,
            error_category="timeout",
            error=f"Plan exceeded its {budget_seconds:g}-second budget.",
            attempts=attempts,
        )
    except asyncio.CancelledError:
        # Preserve task cancellation so client disconnects interrupt the active query.
        raise
    except CatalogueQueryExecutionError as exc:
        category = _failure_category(str(exc))
        return AnalysisPlanOutcome(
            status={
                "cancelled": AnalysisPlanStatus.CANCELLED,
                "memory": AnalysisPlanStatus.MEMORY_EXHAUSTED,
                "timeout": AnalysisPlanStatus.TIMED_OUT,
            }.get(category, AnalysisPlanStatus.FAILED),
            error_category=category,
            error=str(exc),
            attempts=attempts,
        )
    except Exception as exc:
        return AnalysisPlanOutcome(
            status=AnalysisPlanStatus.FAILED,
            error_category="internal",
            error=(
                "Atlas encountered an unexpected error while profiling this "
                f"plan ({type(exc).__name__})."
            ),
            attempts=attempts,
        )


async def _profile(
    runtime: QuackQueryRuntime,
    compilation: CompilationResult,
) -> AnalysisMetrics:
    authored_sql = f"EXPLAIN ANALYZE {compilation.authored_sql}"
    executable_sql = f"EXPLAIN ANALYZE {compilation.executable_sql}"
    profile_compilation = compilation.model_copy(
        update={
            "authored_sql": authored_sql,
            "executable_sql": executable_sql,
        }
    )
    result = await execute_interactive_query(
        runtime,
        authored_sql,
        compilation=profile_compilation,
    )
    if not result.rows or len(result.rows[0]) < 2:
        raise CatalogueQueryExecutionError(
            "EXPLAIN ANALYZE returned no profile."
        )
    raw_profile = result.rows[0][1]
    try:
        profile: dict[str, Any] = (
            json.loads(raw_profile)
            if isinstance(raw_profile, str)
            else raw_profile
        )
    except (TypeError, ValueError) as exc:
        raise CatalogueQueryExecutionError(
            "EXPLAIN ANALYZE returned an invalid profile."
        ) from exc
    return AnalysisMetrics(
        latency_seconds=_float(profile.get("latency")),
        cpu_seconds=_float(profile.get("cpu_time")),
        total_bytes_read=_int(profile.get("total_bytes_read")),
        peak_buffer_memory_bytes=_int(
            profile.get("system_peak_buffer_memory")
        ),
        cumulative_rows_scanned=_int(
            profile.get("cumulative_rows_scanned")
        ),
        cumulative_cardinality=_int(profile.get("cumulative_cardinality")),
        operators=tuple(_operators(profile)),
    )


async def _equivalence_hash(
    runtime: QuackQueryRuntime,
    compilation: CompilationResult,
) -> str:
    result = await execute_interactive_query(
        runtime,
        compilation.authored_sql,
        compilation=compilation,
    )
    return _hash_result(result)


def _hash_result(result: BufferedCatalogueResult) -> str:
    payload = json.dumps(
        {
            "columns": result.columns,
            "types": result.column_types,
            "rows": result.rows,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode()).hexdigest()


def _operators(profile: dict[str, Any]) -> list[AnalysisOperator]:
    found: list[AnalysisOperator] = []

    def visit(node: object, path: str) -> None:
        if not isinstance(node, dict):
            return
        name = node.get("operator_name")
        if name is not None:
            found.append(
                AnalysisOperator(
                    path=path,
                    name=str(name),
                    operator_type=_string(node.get("operator_type")),
                    seconds=_float(node.get("operator_timing")),
                    rows_returned=_int(node.get("operator_cardinality")),
                    rows_scanned=_int(node.get("operator_rows_scanned")),
                )
            )
        children = node.get("children", ())
        if isinstance(children, list):
            for index, child in enumerate(children):
                visit(child, f"{path}.{index}")

    visit(profile, "0")
    return found


def _compare(
    authored: AnalysisPlanOutcome,
    compiled: AnalysisPlanOutcome,
) -> AnalysisComparison:
    before = authored.metrics
    after = compiled.metrics
    assert before is not None and after is not None
    hashes_match = (
        authored.equivalence_hash == compiled.equivalence_hash
        if authored.equivalence_hash is not None
        and compiled.equivalence_hash is not None
        else None
    )
    return AnalysisComparison(
        latency_speedup=_ratio(before.latency_seconds, after.latency_seconds),
        cpu_speedup=_ratio(before.cpu_seconds, after.cpu_seconds),
        rows_scanned_reduction=_reduction(
            before.cumulative_rows_scanned,
            after.cumulative_rows_scanned,
        ),
        bytes_read_reduction=_reduction(
            before.total_bytes_read,
            after.total_bytes_read,
        ),
        peak_buffer_memory_reduction=_reduction(
            before.peak_buffer_memory_bytes,
            after.peak_buffer_memory_bytes,
        ),
        operator_differences=_operator_differences(
            before.operators,
            after.operators,
        ),
        equivalence_hashes_match=hashes_match,
    )


def _operator_differences(
    authored: tuple[AnalysisOperator, ...],
    compiled: tuple[AnalysisOperator, ...],
) -> tuple[AnalysisOperatorDifference, ...]:
    before = {operator.path: operator for operator in authored}
    after = {operator.path: operator for operator in compiled}
    return tuple(
        AnalysisOperatorDifference(
            path=path,
            authored_name=before[path].name if path in before else None,
            compiled_name=after[path].name if path in after else None,
            seconds_delta=_delta(
                before[path].seconds if path in before else None,
                after[path].seconds if path in after else None,
            ),
            rows_scanned_delta=_int_delta(
                before[path].rows_scanned if path in before else None,
                after[path].rows_scanned if path in after else None,
            ),
        )
        for path in sorted(before.keys() | after.keys())
    )


def _failure_category(message: str) -> str:
    lowered = message.lower()
    if "cancel" in lowered or "interrupt" in lowered:
        return "cancelled"
    if "memory" in lowered or "allocation" in lowered:
        return "memory"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    return "execution"


def _ratio(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or after == 0:
        return None
    return before / after


def _reduction(before: int | None, after: int | None) -> float | None:
    if before is None or after is None or before == 0:
        return None
    return (before - after) / before


def _delta(before: float | None, after: float | None) -> float | None:
    return after - before if before is not None and after is not None else None


def _int_delta(before: int | None, after: int | None) -> int | None:
    return after - before if before is not None and after is not None else None


def _float(value: object) -> float | None:
    return float(value) if value is not None else None


def _int(value: object) -> int | None:
    return int(value) if value is not None else None


def _string(value: object) -> str | None:
    return str(value) if value is not None else None
