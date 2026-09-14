"""Validate and bind public SQL, then consider ordered optimization alternatives.

The caller owns the read transaction, snapshot and deadline. Passes never own
connections or commit. Native binding precedes all optimization reads. The first
applied pass wins; each pass sees an independent copy of the original statement.
"""

from dataclasses import dataclass
import re

import duckdb
from sqlglot import exp

from periplus.query.models import Diagnostic, QueryRequest
from periplus.query.optimizations.base import OptimizationPass, PassContext
from periplus.query.validation import _bounded_query, _one_statement

MAX_PLAN_BYTES = 64_000


@dataclass(frozen=True)
class CompiledQuery:
    executable_sql: str
    plan: str
    plan_truncated: bool
    diagnostics: list[Diagnostic]
    optimizations: list[str]
    record_plan: bool


def explain_sql(sql: str, statement: exp.Expression) -> str:
    # Preparing EXPLAIN ANALYZE must not execute its child.
    if sql.lstrip().upper().startswith("EXPLAIN"):
        return "EXPLAIN " + re.sub(
            r"^\s*EXPLAIN\s+(?:ANALYZE\s+)?", "", sql, flags=re.IGNORECASE
        )
    if isinstance(statement, exp.Show):
        return sql
    return "EXPLAIN " + sql


def explain(connection: duckdb.DuckDBPyConnection, sql: str, parameters: list) -> str:
    return "\n".join(
        str(row[-1]) for row in connection.execute(sql, parameters).fetchall()
    )


def compile_query(
    connection: duckdb.DuckDBPyConnection,
    request: QueryRequest,
    *,
    schema: str,
    catalogue_alias: str,
    execute: bool,
    max_rows: int,
    passes: tuple[OptimizationPass, ...],
) -> CompiledQuery:
    executable = _bounded_query(request.sql, max_rows=max_rows, schema=schema)
    statement = _one_statement(request.sql)
    diagnostics = []
    if any(join.args.get("kind") == "CROSS" for join in statement.find_all(exp.Join)):
        diagnostics.append(
            Diagnostic(
                severity="warning",
                code="cartesian_product",
                message="A Cartesian product can require substantial work.",
            )
        )
    plan = explain(connection, explain_sql(request.sql, statement), request.parameters)
    applied = []
    # Inspection statements keep their native semantics and never trigger passes.
    if isinstance(statement, exp.Query):
        for optimization in passes:
            context = PassContext(
                statement.copy(),
                request.parameters,
                schema,
                catalogue_alias,
                connection if execute else None,
            )
            decision = optimization.run(context)
            counts = ", ".join(
                f"{name}={value}" for name, value in sorted(decision.counts.items())
            )
            diagnostics.append(
                Diagnostic(
                    severity="info",
                    code=f"{optimization.name}.{decision.status}.{decision.reason}",
                    message=decision.message + (f" ({counts})" if counts else ""),
                )
            )
            if decision.status == "applied":
                assert decision.statement is not None
                sql = decision.statement.sql(dialect="duckdb")
                # Only trusted passes may introduce private physical scans. Never
                # revalidate generated SQL as public SQL or weaken public admission.
                executable = f"SELECT * FROM ({sql}) AS periplus_console_query LIMIT {max_rows + 1}"
                plan = explain(connection, "EXPLAIN " + sql, request.parameters)
                applied.append(optimization.name)
                break
            if decision.status == "deferred":
                break
    truncated = len(plan.encode()) > MAX_PLAN_BYTES
    if truncated:
        plan = plan.encode()[:MAX_PLAN_BYTES].decode(errors="ignore")
        diagnostics.append(
            Diagnostic(
                severity="warning",
                code="plan_truncated",
                message="The execution plan preview was truncated.",
            )
        )
    return CompiledQuery(
        executable,
        plan,
        truncated,
        diagnostics,
        applied,
        not isinstance(statement, exp.Show),
    )
