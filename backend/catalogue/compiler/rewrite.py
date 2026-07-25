"""Executable SQL rendering for proven catalogue-query plans."""

from __future__ import annotations

import re
from uuid import UUID

from sqlglot import exp, parse_one
from sqlglot.optimizer.scope import traverse_scope

from .plan import CatalogueQueryPlan

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def render_keyed_plan(
    plan: CatalogueQueryPlan,
    *,
    changed_keys_relation: str,
    key_rows: tuple[tuple[object, ...], ...] | None,
) -> str:
    """Push the changed-key predicate into every proven physical scan."""

    if not _SAFE_IDENTIFIER.fullmatch(changed_keys_relation):
        raise ValueError(
            "Changed-keys relation must be an unqualified DuckDB identifier."
        )
    query = parse_one(plan.normalized_sql, dialect="duckdb")
    if not isinstance(query, exp.Select):
        raise RuntimeError("Compiler plan no longer contains a SELECT query.")
    tables = tuple(
        selected
        for scope in traverse_scope(query)
        for _, selected in scope.selected_sources.values()
        if isinstance(selected, exp.Table)
    )

    for index, scan in enumerate(plan.scans):
        if scan.ordinal >= len(tables):
            raise RuntimeError(
                f"Compiler plan scan {scan.ordinal} is no longer present."
            )
        table = tables[scan.ordinal]
        base = table.copy()
        outer_alias = base.args.pop("alias", None)
        if outer_alias is None:
            outer_alias = exp.TableAlias(
                this=exp.to_identifier(scan.alias)
            )
        source_alias = f"_atlas_scope_{index}"
        changed_alias = f"_atlas_changed_{index}"
        base.set(
            "alias",
            exp.TableAlias(
                this=exp.to_identifier(source_alias, quoted=True)
            ),
        )
        if key_rows is None:
            predicates = [
                exp.NullSafeEQ(
                    this=exp.column(
                        binding.relation_column,
                        table=source_alias,
                        quoted=True,
                    ),
                    expression=exp.column(
                        binding.source_column,
                        table=changed_alias,
                        quoted=True,
                    ),
                )
                for binding in scan.key_bindings
            ]
            key_match = _and(predicates)
            changed = exp.Table(
                this=exp.to_identifier(changed_keys_relation, quoted=True),
                alias=exp.TableAlias(
                    this=exp.to_identifier(changed_alias, quoted=True)
                ),
            )
            scope_predicate = exp.Exists(
                this=exp.select("1").from_(changed).where(key_match)
            )
        else:
            row_predicates: list[exp.Expression] = []
            for row in key_rows:
                if len(row) != len(scan.key_bindings):
                    raise ValueError(
                        "Changed-key rows must match the declared key shape."
                    )
                row_predicates.append(
                    _and(
                        [
                            exp.NullSafeEQ(
                                this=exp.column(
                                    binding.relation_column,
                                    table=source_alias,
                                    quoted=True,
                                ),
                                expression=_key_literal(value),
                            )
                            for binding, value in zip(
                                scan.key_bindings,
                                row,
                                strict=True,
                            )
                        ]
                    )
                )
            scope_predicate = (
                exp.or_(*row_predicates)
                if row_predicates
                else exp.false()
            )
        scoped = (
            exp.select(f'"{source_alias}".*')
            .from_(base)
            .where(scope_predicate)
        )
        table.replace(exp.Subquery(this=scoped, alias=outer_alias))
    return query.sql(dialect="duckdb", pretty=True)


def _key_literal(value: object) -> exp.Expression:
    if isinstance(value, UUID):
        return exp.cast(exp.Literal.string(str(value)), "UUID")
    return exp.convert(value)


def _and(predicates: list[exp.Expression]) -> exp.Expression:
    combined = predicates[0]
    for predicate in predicates[1:]:
        combined = exp.and_(combined, predicate)
    return combined
