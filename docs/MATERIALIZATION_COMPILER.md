# Materialization compiler

Atlas users write ordinary DuckDB SQL. The materialization compiler owns the proof that a selected
refresh strategy and stable output key can be evaluated through bounded physical scans. SQL authors
must never add Atlas-only predicates, marker functions, or optimizer hints.

## Query boundary

`materialization.compiler.compile_materialization(...)` is the only public compiler entry point. It
returns an immutable `MaterializationPlan` or raises `IncompatibleQueryError`. The compiler is
fail-closed: an unrecognized construct is incompatible until a test demonstrates a safe plan.

`IncompatibleQueryError.as_dict()` exposes a stable reason code, message, relevant SQL fragment, and
documentation anchor. API and UI consumers may eventually present that payload, but they do not
classify SQL themselves.

## Supported subset

Compiler version 1 intentionally supports only keyed queries with:

- one direct scan of the declared driving table;
- unchanged named column projections and filters without function calls;
- every declared stable key projected unchanged.

Joins, CTEs, subqueries, aggregation, set operations, macros, wildcard projections, append refresh,
and full refresh are rejected with structured diagnostics. Support is added proof by proof rather
than through a permissive fallback.

## Stable output key

Keyed materialization requires at least one unique declared key column. Every key must appear in the
result under the same name and as an unchanged column reference. Expressions, casts, and renamed
keys remain incompatible until lineage analysis can prove their semantics.

## Driving table

The query must directly scan the configured driving table. Compiler version 1 accepts exactly one
physical relation scan; later versions will derive key equivalence through joins, CTE projections,
and expanded macro definitions.

## Test-driven extension

Each new feature begins as a structured incompatibility case. Its implementation must add:

1. a plan-structure test proving the intended lineage;
2. a semantic test comparing the original and compiled query for bounded keys;
3. negative cases for joins, nulls, aliases, and other boundaries affected by the feature.

Tests should assert plan structure and row equivalence instead of relying primarily on generated SQL
snapshots.

## Upstream boundaries

When safe compilation depends on DuckDB, DuckLake, DuckBasin, or Quack behavior, record the minimal
reproduction and required upstream contract in `UPSTREAM.md`. Compiler diagnostics should link the
corresponding documentation anchor; Atlas-specific SQL workarounds are not part of the user
contract.
