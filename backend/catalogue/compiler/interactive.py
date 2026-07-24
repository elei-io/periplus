"""Semantics-preserving rewrites for interactive catalogue queries."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum

from sqlglot import exp
from sqlglot.optimizer.scope import Scope

from .analysis import AnalyzedCatalogueQuery, analyze_resolved_query

_PUSHDOWN_BARRIERS = (
    "distinct",
    "group",
    "having",
    "limit",
    "offset",
    "qualify",
    "windows",
)
_VOLATILE_FUNCTION_TYPES = (
    exp.CurrentDate,
    exp.CurrentDatetime,
    exp.CurrentTime,
    exp.CurrentTimestamp,
    exp.CurrentUser,
    exp.NextValueFor,
    exp.Rand,
    exp.SessionUser,
    exp.Uuid,
)
_VOLATILE_FUNCTION_NAMES = frozenset(
    {
        "currval",
        "gen_random_uuid",
        "nextval",
        "now",
        "setval",
        "today",
    }
)
_DETERMINISTIC_ROW_LOCAL_FUNCTION_TYPES = (
    exp.Abs,
    exp.And,
    exp.Case,
    exp.Cast,
    exp.Ceil,
    exp.Coalesce,
    exp.Concat,
    exp.ConcatWs,
    exp.Extract,
    exp.Floor,
    exp.Greatest,
    exp.If,
    exp.Least,
    exp.Left,
    exp.Length,
    exp.Lower,
    exp.Not,
    exp.Nullif,
    exp.Or,
    exp.Replace,
    exp.Right,
    exp.Round,
    exp.StrPosition,
    exp.Substring,
    exp.TimestampTrunc,
    exp.Trim,
    exp.TryCast,
    exp.Upper,
)

class InteractiveRewrite(StrEnum):
    BOUNDED_SCALAR_INPUT = "bounded_scalar_input"
    REPEATED_DERIVED_SCAN = "repeated_derived_scan"
    REDUNDANT_SCOPE = "redundant_scope"
    LEFT_JOIN_PREDICATE = "left_join_predicate"
    INNER_JOIN_PREDICATE = "inner_join_predicate"
    PRE_EXPANSION_PREDICATE = "pre_expansion_predicate"
    UNION_ALL_PREDICATE = "union_all_predicate"
    PREDICATE_PUSHDOWN = "predicate_pushdown"
    AGGREGATE_INPUT_FILTER = "aggregate_input_filter"
    HAVING_PUSHDOWN = "having_pushdown"
    WINDOW_PARTITION_FILTER = "window_partition_filter"
    PROJECTION_PRUNING = "projection_pruning"
    TOP_K = "top_k"
    SAFE_LIMIT = "safe_limit"
    REDUNDANT_SORT = "redundant_sort"
    REPEATED_SCALAR_EXPRESSION = "repeated_scalar_expression"
    CTE_MATERIALIZATION = "cte_materialization"


@dataclass(frozen=True, slots=True)
class AppliedInteractiveRewrite:
    rule: InteractiveRewrite
    evidence: str


@dataclass(frozen=True, slots=True)
class InteractiveCompilation:
    sql: str
    applied_rewrites: tuple[AppliedInteractiveRewrite, ...]


_REWRITE_EVIDENCE = {
    InteractiveRewrite.BOUNDED_SCALAR_INPUT: (
        "A materialized ORDER BY/LIMIT/OFFSET boundary selects source rows "
        "before deterministic scalar macro evaluation."
    ),
    InteractiveRewrite.REPEATED_DERIVED_SCAN: (
        "Identical deterministic scans share one totally ordered row context."
    ),
    InteractiveRewrite.REDUNDANT_SCOPE: (
        "A single-use direct projection preserves names and row semantics."
    ),
    InteractiveRewrite.LEFT_JOIN_PREDICATE: (
        "Preserved-side lineage safely constrains the nullable input."
    ),
    InteractiveRewrite.INNER_JOIN_PREDICATE: (
        "Inner equijoin lineage proves equivalent predicate columns."
    ),
    InteractiveRewrite.PRE_EXPANSION_PREDICATE: (
        "The predicate depends only on the deterministic pre-expansion row."
    ),
    InteractiveRewrite.UNION_ALL_PREDICATE: (
        "Every positional UNION ALL branch exposes the filtered direct column."
    ),
    InteractiveRewrite.PREDICATE_PUSHDOWN: (
        "Single-use direct projection lineage preserves the predicate."
    ),
    InteractiveRewrite.AGGREGATE_INPUT_FILTER: (
        "The predicate depends only on direct grouping-key outputs."
    ),
    InteractiveRewrite.HAVING_PUSHDOWN: (
        "The HAVING conjunct depends only on direct grouping keys."
    ),
    InteractiveRewrite.WINDOW_PARTITION_FILTER: (
        "The predicate covers direct columns common to every window partition."
    ),
    InteractiveRewrite.PROJECTION_PRUNING: (
        "Consumer lineage proves the removed direct outputs are unused."
    ),
    InteractiveRewrite.TOP_K: (
        "A total output order and literal bound preserve the selected prefix."
    ),
    InteractiveRewrite.SAFE_LIMIT: (
        "A total child order and literal bound preserve the outer slice."
    ),
    InteractiveRewrite.REDUNDANT_SORT: (
        "Every consumer supplies a total result order."
    ),
    InteractiveRewrite.REPEATED_SCALAR_EXPRESSION: (
        "Exact deterministic projections share one equivalent row context."
    ),
    InteractiveRewrite.CTE_MATERIALIZATION: (
        "Deterministic CTE reuse count and total result ordering determine the boundary."
    ),
}


def compile_interactive_query(analysis: AnalyzedCatalogueQuery) -> str:
    """Return ordinary DuckDB SQL with proven interactive rewrites."""

    return compile_interactive_query_with_explanation(analysis).sql


def compile_interactive_query_with_explanation(
    analysis: AnalyzedCatalogueQuery,
) -> InteractiveCompilation:
    """Return optimized SQL plus bounded evidence for rules that changed it."""

    query = analysis.query.copy()
    # Scope objects refer to the original tree, so rebuild them for the copy.
    resolved = analyze_resolved_query(query)
    applied: list[AppliedInteractiveRewrite] = []
    if analysis.bounded_scalar_input:
        applied.append(
            AppliedInteractiveRewrite(
                rule=InteractiveRewrite.BOUNDED_SCALAR_INPUT,
                evidence=_REWRITE_EVIDENCE[
                    InteractiveRewrite.BOUNDED_SCALAR_INPUT
                ],
            )
        )

    def record(
        rule: InteractiveRewrite,
        before: str,
    ) -> None:
        if before == resolved.normalized_sql:
            return
        applied.append(
            AppliedInteractiveRewrite(
                rule=rule,
                evidence=_REWRITE_EVIDENCE[rule],
            )
        )

    before = resolved.normalized_sql
    for _ in range(len(resolved.scopes)):
        if not _hoist_repeated_derived_scan(resolved):
            break
        resolved = analyze_resolved_query(resolved.query)
    record(InteractiveRewrite.REPEATED_DERIVED_SCAN, before)
    before = resolved.normalized_sql
    for _ in range(len(resolved.scopes)):
        if not _eliminate_redundant_derived_scope(resolved):
            break
        resolved = analyze_resolved_query(resolved.query)
    record(InteractiveRewrite.REDUNDANT_SCOPE, before)
    before = resolved.normalized_sql
    _propagate_left_join_predicates(resolved)
    record(InteractiveRewrite.LEFT_JOIN_PREDICATE, before)
    before = resolved.normalized_sql
    _propagate_inner_join_predicates(resolved)
    record(InteractiveRewrite.INNER_JOIN_PREDICATE, before)
    before = resolved.normalized_sql
    _push_pre_expansion_predicates(resolved)
    record(InteractiveRewrite.PRE_EXPANSION_PREDICATE, before)
    before = resolved.normalized_sql
    _push_union_all_predicates(resolved)
    record(InteractiveRewrite.UNION_ALL_PREDICATE, before)
    before = resolved.normalized_sql
    _push_predicates(resolved)
    record(InteractiveRewrite.PREDICATE_PUSHDOWN, before)
    before = resolved.normalized_sql
    _push_aggregate_input_predicates(resolved)
    record(InteractiveRewrite.AGGREGATE_INPUT_FILTER, before)
    before = resolved.normalized_sql
    _push_having_group_predicates(resolved)
    record(InteractiveRewrite.HAVING_PUSHDOWN, before)
    before = resolved.normalized_sql
    _push_window_partition_predicates(resolved)
    record(InteractiveRewrite.WINDOW_PARTITION_FILTER, before)
    before = resolved.normalized_sql
    for _ in range(len(resolved.scopes)):
        if not _prune_projections(resolved):
            break
        resolved = analyze_resolved_query(resolved.query)
    record(InteractiveRewrite.PROJECTION_PRUNING, before)
    before = resolved.normalized_sql
    _push_top_k(resolved)
    record(InteractiveRewrite.TOP_K, before)
    before = resolved.normalized_sql
    _push_safe_limits(resolved)
    record(InteractiveRewrite.SAFE_LIMIT, before)
    before = resolved.normalized_sql
    _remove_redundant_sorts(resolved)
    record(InteractiveRewrite.REDUNDANT_SORT, before)
    before = resolved.normalized_sql
    _share_repeated_scalar_expressions(resolved)
    record(InteractiveRewrite.REPEATED_SCALAR_EXPRESSION, before)
    before = resolved.normalized_sql
    _choose_cte_materialization(resolved)
    record(InteractiveRewrite.CTE_MATERIALIZATION, before)
    return InteractiveCompilation(
        sql=resolved.normalized_sql,
        applied_rewrites=tuple(applied),
    )


def _share_repeated_scalar_expressions(
    analysis: AnalyzedCatalogueQuery,
) -> None:
    scope = next(
        (
            candidate
            for candidate in reversed(analysis.scopes)
            if candidate.expression is analysis.query
        ),
        None,
    )
    if scope is None or not _safe_expression_sharing_scope(scope):
        return
    expression = scope.expression
    assert isinstance(expression, exp.Select)
    source_alias, (_, source) = next(iter(scope.selected_sources.items()))
    assert isinstance(source, exp.Table)
    repeated: dict[str, list[exp.Expression]] = defaultdict(list)
    candidates: dict[str, exp.Expression] = {}
    for selection in expression.expressions:
        projected = (
            selection.this if isinstance(selection, exp.Alias) else selection
        )
        if (
            isinstance(projected, (exp.Column, exp.Literal, exp.Star))
            or not _predicate_is_rewrite_safe(projected)
        ):
            continue
        columns = tuple(projected.find_all(exp.Column))
        if (
            not columns
            or any(
                column.table
                and column.table.lower() != source_alias.lower()
                for column in columns
            )
        ):
            continue
        signature = projected.sql(dialect="duckdb", pretty=False)
        repeated[signature].append(selection)
        candidates[signature] = projected
    groups = [
        (candidates[signature], selections)
        for signature, selections in repeated.items()
        if len(selections) >= 2
    ]
    if not groups:
        return
    used_aliases = {
        relation.alias_or_name.lower()
        for relation in _select_relations(expression)
        if relation.alias_or_name
    }
    for candidate, selections in groups:
        relation_alias = _unused_expression_relation_name(used_aliases)
        used_aliases.add(relation_alias)
        value_name = "value"

        def qualify_column(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Column) and not node.table:
                qualified = node.copy()
                qualified.set(
                    "table",
                    exp.to_identifier(source_alias),
                )
                return qualified
            return node

        shared_expression = candidate.copy().transform(qualify_column)
        lateral = exp.Lateral(
            this=exp.Subquery(
                this=exp.select(
                    exp.alias_(
                        shared_expression,
                        value_name,
                        copy=False,
                    )
                )
            ),
            alias=exp.TableAlias(
                this=exp.to_identifier(relation_alias),
            ),
        )
        expression.append(
            "joins",
            exp.Join(this=lateral, kind="CROSS"),
        )
        for selection in selections:
            replacement = exp.column(
                value_name,
                table=relation_alias,
            )
            if isinstance(selection, exp.Alias):
                selection.set("this", replacement)
            else:
                selection.replace(replacement)


def _safe_expression_sharing_scope(scope: Scope) -> bool:
    expression = scope.expression
    if (
        not isinstance(expression, exp.Select)
        or scope.outer_columns
        or len(scope.selected_sources) != 1
        or expression.args.get("joins")
        or not _consumer_orders_every_output(scope)
        or _has_unapproved_scalar_function(expression)
        or any(
            expression.args.get(argument) is not None
            for argument in (
                "distinct",
                "where",
                "group",
                "having",
                "qualify",
                "limit",
                "offset",
                "windows",
                "with_",
                "hint",
                "exclude",
                "operation_modifiers",
            )
        )
    ):
        return False
    _, (_, source) = next(iter(scope.selected_sources.items()))
    return (
        isinstance(source, exp.Table)
        and isinstance(source.this, exp.Identifier)
        and source.args.get("sample") is None
        and not source.args.get("pivots")
    )


def _consumer_orders_every_output(scope: Scope) -> bool:
    expression = scope.expression
    if (
        not isinstance(expression, exp.Select)
        or expression.args.get("order") is None
        or expression.args.get("distinct") is not None
        or _has_unapproved_scalar_function(expression)
    ):
        return False
    output_names = [
        selection.alias_or_name.lower()
        for selection in expression.expressions
    ]
    if (
        not output_names
        or any(not output_name for output_name in output_names)
        or len(output_names) != len(set(output_names))
    ):
        return False
    ordered_outputs: set[str] = set()
    for ordered in expression.args["order"].expressions:
        ordering = (
            ordered.this if isinstance(ordered, exp.Ordered) else ordered
        )
        if isinstance(ordering, exp.Literal) and ordering.is_int:
            index = int(ordering.this) - 1
            if not 0 <= index < len(output_names):
                return False
            ordered_outputs.add(output_names[index])
            continue
        if (
            not isinstance(ordering, exp.Column)
            or ordering.table
            or ordering.name.lower() not in output_names
        ):
            return False
        ordered_outputs.add(ordering.name.lower())
    return ordered_outputs == set(output_names)


def _unused_expression_relation_name(used: set[str]) -> str:
    index = 1
    while True:
        candidate = f"_atlas_interactive_expression_{index}"
        if candidate not in used:
            return candidate
        index += 1


def _choose_cte_materialization(
    analysis: AnalyzedCatalogueQuery,
) -> None:
    root = next(
        (
            scope
            for scope in reversed(analysis.scopes)
            if scope.expression is analysis.query
        ),
        None,
    )
    if (
        root is None
        or not _consumer_totally_orders(root)
        or any(
            with_clause.args.get("recursive")
            for with_clause in analysis.query.find_all(exp.With)
        )
    ):
        return
    uses = Counter(
        id(source)
        for scope in analysis.scopes
        for _, source in scope.selected_sources.values()
        if isinstance(source, Scope)
    )
    for scope in analysis.scopes:
        cte = scope.expression.parent
        if (
            not isinstance(cte, exp.CTE)
            or cte.args.get("materialized") is not None
            or not _reusable_derived_scan(scope)
        ):
            continue
        use_count = uses[id(scope)]
        if use_count:
            cte.set("materialized", use_count > 1)


def _hoist_repeated_derived_scan(
    analysis: AnalyzedCatalogueQuery,
) -> bool:
    root = next(
        (
            scope
            for scope in reversed(analysis.scopes)
            if scope.expression is analysis.query
        ),
        None,
    )
    if root is None or not _consumer_totally_orders(root):
        return False
    relations: dict[str, list[exp.Subquery]] = defaultdict(list)
    for relation in _select_relations(root.expression):
        if not isinstance(relation, exp.Subquery):
            continue
        alias = relation.alias_or_name.lower()
        source = root.sources.get(alias)
        if (
            relation.args.get("alias") is None
            or not isinstance(source, Scope)
            or not _reusable_derived_scan(source)
        ):
            continue
        signature = source.expression.sql(
            dialect="duckdb",
            pretty=False,
        )
        relations[signature].append(relation)
    repeated = next(
        (
            group
            for group in relations.values()
            if len(group) >= 2
        ),
        None,
    )
    if repeated is None:
        return False
    cte_name = _unused_generated_cte_name(analysis.query)
    definition = repeated[0].this.copy()
    cte = exp.CTE(
        this=definition,
        alias=exp.TableAlias(this=exp.to_identifier(cte_name)),
        materialized=True,
    )
    with_clause = analysis.query.args.get("with_")
    if with_clause is None:
        analysis.query.set("with_", exp.With(expressions=[cte]))
    else:
        with_clause.append("expressions", cte)
    for relation in repeated:
        replacement = exp.Table(
            this=exp.to_identifier(cte_name),
            alias=relation.args["alias"].copy(),
        )
        _replace_derived_relation(
            root.expression,
            relation=relation,
            replacement=replacement,
        )
    return True


def _select_relations(expression: exp.Select) -> tuple[exp.Expression, ...]:
    from_clause = expression.args.get("from_")
    return (
        *((from_clause.this,) if from_clause is not None else ()),
        *(join.this for join in expression.args.get("joins") or ()),
    )


def _reusable_derived_scan(scope: Scope) -> bool:
    expression = scope.expression
    if (
        not isinstance(expression, exp.Select)
        or scope.outer_columns
        or len(scope.selected_sources) != 1
        or expression.args.get("joins")
        or any(
            expression.args.get(argument) is not None
            for argument in (
                "distinct",
                "group",
                "having",
                "qualify",
                "order",
                "limit",
                "offset",
                "windows",
                "with_",
                "hint",
                "exclude",
                "operation_modifiers",
            )
        )
        or _has_unapproved_scalar_function(expression)
    ):
        return False
    _, (_, source) = next(iter(scope.selected_sources.items()))
    if (
        not isinstance(source, exp.Table)
        or not isinstance(source.this, exp.Identifier)
        or source.args.get("sample") is not None
        or source.args.get("pivots")
    ):
        return False
    projections = _direct_projections(scope)
    if (
        projections is None
        or len(projections) != len(expression.expressions)
    ):
        return False
    where = expression.args.get("where")
    return where is None or all(
        _predicate_is_rewrite_safe(predicate)
        for predicate in _conjuncts(where.this)
    )


def _unused_generated_cte_name(query: exp.Query) -> str:
    used = {
        table.name.lower()
        for table in query.find_all(exp.Table)
    }
    used.update(
        cte.alias_or_name.lower()
        for cte in query.find_all(exp.CTE)
    )
    index = 1
    while True:
        candidate = f"_atlas_interactive_shared_{index}"
        if candidate not in used:
            return candidate
        index += 1


def _eliminate_redundant_derived_scope(
    analysis: AnalyzedCatalogueQuery,
) -> bool:
    uses = Counter(
        id(source)
        for scope in analysis.scopes
        for _, source in scope.selected_sources.values()
        if isinstance(source, Scope)
    )
    for parent in reversed(analysis.scopes):
        if not isinstance(parent.expression, exp.Select):
            continue
        if any(
            join.args.get("using")
            for join in parent.expression.args.get("joins") or ()
        ):
            continue
        for alias, (_, child) in parent.selected_sources.items():
            if (
                not isinstance(child, Scope)
                or uses[id(child)] != 1
                or not _pure_direct_projection_scope(child)
            ):
                continue
            relation = _derived_relation(parent.expression, alias)
            if (
                relation is None
                or relation.this is not child.expression
                or relation.args.get("alias") is None
            ):
                continue
            projections = _direct_projections(child)
            if projections is None or not projections:
                continue
            columns = _local_expressions(parent.expression, exp.Column)
            selected_aliases = {
                selected_alias.lower()
                for selected_alias in parent.selected_sources
            }
            if any(
                isinstance(selection, exp.Star)
                or (
                    isinstance(selection, exp.Column)
                    and selection.is_star
                )
                for selection in parent.expression.expressions
            ):
                continue
            if any(
                (not column.table and len(selected_aliases) != 1)
                or
                (
                    column.table
                    and column.table.lower() == alias.lower()
                    and column.name.lower() not in projections
                )
                or (
                    not column.table
                    and len(selected_aliases) == 1
                    and column.name.lower() not in projections
                )
                for column in columns
            ):
                continue
            if not _parent_selections_preserve_names(
                parent.expression,
                alias=alias.lower(),
                only_source=len(selected_aliases) == 1,
            ):
                continue
            inner_from = child.expression.args.get("from_")
            if inner_from is None or not isinstance(inner_from.this, exp.Table):
                continue
            _rewrite_parent_columns(
                parent.expression,
                alias=alias.lower(),
                only_source=len(selected_aliases) == 1,
                projections=projections,
            )
            replacement = inner_from.this.copy()
            replacement.set("alias", relation.args.get("alias").copy())
            _replace_derived_relation(
                parent.expression,
                relation=relation,
                replacement=replacement,
            )
            return True
    return False


def _pure_direct_projection_scope(scope: Scope) -> bool:
    expression = scope.expression
    if (
        not isinstance(expression, exp.Select)
        or scope.outer_columns
        or len(expression.args.get("joins") or ()) != 0
        or len(scope.selected_sources) != 1
        or not all(
            isinstance(source, exp.Table)
            for _, source in scope.selected_sources.values()
        )
        or any(
            expression.args.get(argument) is not None
            for argument in (
                "distinct",
                "where",
                "group",
                "having",
                "qualify",
                "order",
                "limit",
                "offset",
                "windows",
                "with_",
                "hint",
                "exclude",
                "operation_modifiers",
            )
        )
    ):
        return False
    projections = _direct_projections(scope)
    return (
        projections is not None
        and len(projections) == len(expression.expressions)
    )


def _derived_relation(
    expression: exp.Select,
    alias: str,
) -> exp.Subquery | None:
    from_clause = expression.args.get("from_")
    relations = (
        *((from_clause.this,) if from_clause is not None else ()),
        *(join.this for join in expression.args.get("joins") or ()),
    )
    for relation in relations:
        if (
            isinstance(relation, exp.Subquery)
            and relation.alias_or_name.lower() == alias.lower()
        ):
            return relation
    return None


def _parent_selections_preserve_names(
    expression: exp.Select,
    *,
    alias: str,
    only_source: bool,
) -> bool:
    for selection in expression.expressions:
        if isinstance(selection, exp.Alias):
            continue
        columns = tuple(_local_expressions(selection, exp.Column))
        if not any(
            (column.table and column.table.lower() == alias)
            or (not column.table and only_source)
            for column in columns
        ):
            continue
        if not isinstance(selection, exp.Column):
            return False
    return True


def _rewrite_parent_columns(
    expression: exp.Select,
    *,
    alias: str,
    only_source: bool,
    projections: dict[str, exp.Column],
) -> None:
    output_names = {
        id(selection): selection.alias_or_name
        for selection in expression.expressions
        if not isinstance(selection, exp.Alias)
        and isinstance(selection, exp.Column)
    }
    for column in _local_expressions(expression, exp.Column):
        if (
            column.table
            and column.table.lower() == alias
        ) or (not column.table and only_source):
            column.set(
                "this",
                projections[column.name.lower()].this.copy(),
            )
    rewritten_selections: list[exp.Expression] = []
    for selection in expression.expressions:
        output_name = output_names.get(id(selection))
        if (
            output_name
            and isinstance(selection, exp.Column)
            and selection.name.lower() != output_name.lower()
        ):
            rewritten_selections.append(
                exp.alias_(selection, output_name, copy=False)
            )
        else:
            rewritten_selections.append(selection)
    expression.set("expressions", rewritten_selections)


def _replace_derived_relation(
    expression: exp.Select,
    *,
    relation: exp.Subquery,
    replacement: exp.Table,
) -> None:
    from_clause = expression.args.get("from_")
    if from_clause is not None and from_clause.this is relation:
        from_clause.set("this", replacement)
        return
    for join in expression.args.get("joins") or ():
        if join.this is relation:
            join.set("this", replacement)
            return


def _propagate_left_join_predicates(
    analysis: AnalyzedCatalogueQuery,
) -> None:
    uses = Counter(
        id(source)
        for scope in analysis.scopes
        for source in scope.sources.values()
        if isinstance(source, Scope)
    )
    for scope in analysis.scopes:
        if not isinstance(scope.expression, exp.Select):
            continue
        where = scope.expression.args.get("where")
        if where is None:
            continue
        from_clause = scope.expression.args.get("from_")
        available: list[str] = []
        if from_clause is not None and from_clause.this is not None:
            available.append(from_clause.this.alias_or_name.lower())
        for join in scope.expression.args.get("joins") or ():
            right_alias = join.this.alias_or_name.lower()
            side = str(join.args.get("side") or "").upper()
            kind = str(join.args.get("kind") or "").upper()
            if side == "LEFT" and kind in {"", "OUTER"}:
                mappings = _left_join_mappings(
                    join,
                    available=available,
                    right_alias=right_alias,
                )
                derived = _derive_right_join_predicates(
                    where.this,
                    mappings=mappings,
                    available=frozenset(available),
                    right_alias=right_alias,
                )
                if derived:
                    source = scope.sources.get(right_alias)
                    pushed_into_source = False
                    if (
                        isinstance(source, Scope)
                        and uses[id(source)] == 1
                        and _safe_pushdown_scope(source)
                    ):
                        projections = _direct_projections(source)
                        if projections is not None:
                            for predicate in derived:
                                rewritten = _rewrite_predicate(
                                    predicate,
                                    alias=right_alias,
                                    projections=projections,
                                )
                                if rewritten is not None:
                                    _append_scope_predicate(source, rewritten)
                                    pushed_into_source = True
                    using = join.args.get("using") or ()
                    if using and isinstance(source, exp.Table):
                        _wrap_join_table(
                            join,
                            predicates=derived,
                            right_alias=right_alias,
                        )
                        pushed_into_source = True
                    if not using:
                        condition = join.args.get("on")
                        for predicate in derived:
                            condition = (
                                exp.and_(condition.copy(), predicate.copy())
                                if condition is not None
                                else predicate.copy()
                            )
                        join.set("on", condition)
                    elif not pushed_into_source:
                        # A shared or semantically barred USING source cannot
                        # receive the derived predicate without changing all
                        # consumers or replacing USING output semantics.
                        pass
            available.append(right_alias)


def _push_pre_expansion_predicates(
    analysis: AnalyzedCatalogueQuery,
) -> None:
    for scope in analysis.scopes:
        expression = scope.expression
        if not isinstance(expression, exp.Select):
            continue
        where = expression.args.get("where")
        base_alias = _pre_expansion_base_alias(expression)
        if where is None or base_alias is None:
            continue
        source = scope.sources.get(base_alias)
        if source is None:
            continue
        predicates: list[exp.Expression] = []
        for predicate in _conjuncts(where.this):
            if not _predicate_is_rewrite_safe(predicate):
                continue
            columns = tuple(predicate.find_all(exp.Column))
            if (
                not columns
                or any(
                    column.table
                    and column.table.lower() != base_alias
                    for column in columns
                )
                or (
                    len(scope.selected_sources) != 1
                    and any(not column.table for column in columns)
                )
            ):
                continue
            predicates.append(predicate)
        if not predicates:
            continue
        if isinstance(source, Scope):
            if (
                _derived_relation(expression, base_alias) is None
                or not _safe_pushdown_scope(source)
            ):
                continue
            projections = _direct_projections(source)
            if projections is None:
                continue
            for predicate in predicates:
                rewritten = _rewrite_predicate(
                    predicate,
                    alias=base_alias,
                    projections=projections,
                )
                if rewritten is not None:
                    _append_scope_predicate(source, rewritten)
            continue
        if (
            isinstance(source, exp.Table)
            and isinstance(source.this, exp.Identifier)
            and (
                source.args.get("alias") is None
                or not source.args["alias"].args.get("columns")
            )
        ):
            _wrap_pre_expansion_table(
                expression,
                alias=base_alias,
                predicates=tuple(predicates),
            )


def _pre_expansion_base_alias(expression: exp.Select) -> str | None:
    has_projection_expansion = any(
        next(selection.find_all((exp.Explode, exp.Unnest)), None)
        is not None
        for selection in expression.expressions
    )
    has_join_expansion = any(
        isinstance(join.this, (exp.Unnest, exp.Lateral))
        and next(join.this.find_all(exp.Unnest), None) is not None
        for join in expression.args.get("joins") or ()
    )
    if not has_projection_expansion and not has_join_expansion:
        return None
    from_clause = expression.args.get("from_")
    if from_clause is None or from_clause.this is None:
        return None
    return from_clause.this.alias_or_name.lower()


def _wrap_pre_expansion_table(
    expression: exp.Select,
    *,
    alias: str,
    predicates: tuple[exp.Expression, ...],
) -> None:
    from_clause = expression.args.get("from_")
    if (
        from_clause is None
        or not isinstance(from_clause.this, exp.Table)
        or from_clause.this.alias_or_name.lower() != alias
    ):
        return
    table = from_clause.this
    base = table.copy()
    outer_alias = base.args.pop("alias", None)
    if outer_alias is None:
        outer_alias = exp.TableAlias(this=exp.to_identifier(alias))
    inner_alias = "_atlas_interactive_input"
    base.set(
        "alias",
        exp.TableAlias(this=exp.to_identifier(inner_alias, quoted=True)),
    )
    rewritten: list[exp.Expression] = []
    for predicate in predicates:
        rewritten.append(
            predicate.copy().transform(
                lambda node: (
                    exp.column(
                        node.name,
                        table=inner_alias,
                        quoted=True,
                    )
                    if isinstance(node, exp.Column)
                    else node
                )
            )
        )
    combined = rewritten[0]
    for predicate in rewritten[1:]:
        combined = exp.and_(combined, predicate)
    scoped = (
        exp.select(f'"{inner_alias}".*')
        .from_(base)
        .where(combined)
    )
    from_clause.set(
        "this",
        exp.Subquery(this=scoped, alias=outer_alias),
    )


def _wrap_join_table(
    join: exp.Join,
    *,
    predicates: tuple[exp.Expression, ...],
    right_alias: str,
) -> None:
    table = join.this
    if not isinstance(table, exp.Table):
        return
    base = table.copy()
    outer_alias = base.args.pop("alias", None)
    if outer_alias is None:
        outer_alias = exp.TableAlias(this=exp.to_identifier(right_alias))
    inner_alias = "_atlas_interactive_right"
    base.set(
        "alias",
        exp.TableAlias(this=exp.to_identifier(inner_alias, quoted=True)),
    )
    rewritten: list[exp.Expression] = []
    for predicate in predicates:
        rewritten.append(
            predicate.copy().transform(
                lambda node: (
                    exp.column(
                        node.name,
                        table=inner_alias,
                        quoted=True,
                    )
                    if isinstance(node, exp.Column)
                    and node.table.lower() == right_alias
                    else node
                )
            )
        )
    combined = rewritten[0]
    for predicate in rewritten[1:]:
        combined = exp.and_(combined, predicate)
    scoped = (
        exp.select(f'"{inner_alias}".*')
        .from_(base)
        .where(combined)
    )
    join.set("this", exp.Subquery(this=scoped, alias=outer_alias))


def _left_join_mappings(
    join: exp.Join,
    *,
    available: list[str],
    right_alias: str,
) -> dict[tuple[str, str], tuple[str, str]]:
    mappings: dict[tuple[str, str], tuple[str, str]] = {}
    for identifier in join.args.get("using") or ():
        if not isinstance(identifier, exp.Identifier):
            continue
        for left_alias in available:
            mappings[(left_alias, identifier.name.lower())] = (
                right_alias,
                identifier.name.lower(),
            )
    condition = join.args.get("on")
    for predicate in _conjuncts(condition) if condition else ():
        if not isinstance(predicate, (exp.EQ, exp.NullSafeEQ)):
            continue
        left = predicate.this
        right = predicate.expression
        if not (
            isinstance(left, exp.Column)
            and isinstance(right, exp.Column)
            and left.table
            and right.table
        ):
            continue
        left_node = (left.table.lower(), left.name.lower())
        right_node = (right.table.lower(), right.name.lower())
        if left_node[0] == right_alias and right_node[0] in available:
            mappings[right_node] = left_node
        elif right_node[0] == right_alias and left_node[0] in available:
            mappings[left_node] = right_node
    return mappings


def _derive_right_join_predicates(
    where: exp.Expression,
    *,
    mappings: dict[tuple[str, str], tuple[str, str]],
    available: frozenset[str],
    right_alias: str,
) -> tuple[exp.Expression, ...]:
    derived: list[exp.Expression] = []
    for predicate in _conjuncts(where):
        columns = tuple(predicate.find_all(exp.Column))
        if (
            not columns
            or any(not column.table for column in columns)
            or any(column.table.lower() not in available for column in columns)
            or not _predicate_is_rewrite_safe(predicate)
        ):
            continue
        replacements: dict[tuple[str, str], exp.Column] = {}
        for column in columns:
            source = (column.table.lower(), column.name.lower())
            target = mappings.get(source)
            if target is None:
                break
            replacements[source] = exp.column(target[1], table=right_alias)
        else:
            derived.append(
                predicate.copy().transform(
                    lambda node: (
                        replacements[
                            (node.table.lower(), node.name.lower())
                        ].copy()
                        if isinstance(node, exp.Column)
                        else node
                    )
                )
            )
    return tuple(derived)


def _propagate_inner_join_predicates(
    analysis: AnalyzedCatalogueQuery,
) -> None:
    for scope in analysis.scopes:
        if not isinstance(scope.expression, exp.Select):
            continue
        where = scope.expression.args.get("where")
        if where is None:
            continue
        equivalences: dict[
            tuple[str, str],
            frozenset[tuple[str, str]],
        ] = {}
        for component in analysis.facts_for(scope).equivalence_classes:
            rendered = frozenset(
                (column.relation, column.column) for column in component
            )
            for column in component:
                equivalences[(column.relation, column.column)] = rendered
        if not equivalences:
            continue
        existing = {
            predicate.sql(dialect="duckdb")
            for predicate in _conjuncts(where.this)
        }
        derived: list[exp.Expression] = []
        for predicate in _conjuncts(where.this):
            columns = tuple(predicate.find_all(exp.Column))
            if (
                not columns
                or any(not column.table for column in columns)
                or len({column.table.lower() for column in columns}) != 1
                or not _predicate_is_rewrite_safe(predicate)
            ):
                continue
            source_alias = columns[0].table.lower()
            for target_alias in scope.selected_sources:
                target_alias = target_alias.lower()
                if target_alias == source_alias:
                    continue
                replacements: dict[tuple[str, str], exp.Column] = {}
                for column in columns:
                    source = (source_alias, column.name.lower())
                    candidates = tuple(
                        candidate
                        for candidate in equivalences.get(source, ())
                        if candidate[0] == target_alias
                    )
                    if len(candidates) != 1:
                        break
                    replacements[source] = exp.column(
                        candidates[0][1],
                        table=target_alias,
                    )
                else:
                    rewritten = predicate.copy().transform(
                        lambda node: (
                            replacements[
                                (node.table.lower(), node.name.lower())
                            ].copy()
                            if isinstance(node, exp.Column)
                            else node
                        )
                    )
                    rendered = rewritten.sql(dialect="duckdb")
                    if rendered not in existing:
                        existing.add(rendered)
                        derived.append(rewritten)
        if derived:
            combined = where.this.copy()
            for predicate in derived:
                combined = exp.and_(combined, predicate)
            scope.expression.set("where", exp.Where(this=combined))


def _push_predicates(analysis: AnalyzedCatalogueQuery) -> None:
    uses = Counter(
        id(source)
        for scope in analysis.scopes
        for source in scope.sources.values()
        if isinstance(source, Scope)
    )
    for scope in reversed(analysis.scopes):
        if not isinstance(scope.expression, exp.Select):
            continue
        where = scope.expression.args.get("where")
        if where is None:
            continue
        for predicate in _conjuncts(where.this):
            target = _predicate_target(scope, predicate)
            if target is None:
                continue
            alias, child = target
            if uses[id(child)] != 1 or not _safe_pushdown_scope(child):
                continue
            projections = _direct_projections(child)
            if projections is None:
                continue
            rewritten = _rewrite_predicate(
                predicate,
                alias=alias,
                projections=projections,
            )
            if rewritten is None:
                continue
            _append_scope_predicate(child, rewritten)


def _push_union_all_predicates(
    analysis: AnalyzedCatalogueQuery,
) -> None:
    uses = Counter(
        id(source)
        for scope in analysis.scopes
        for source in scope.sources.values()
        if isinstance(source, Scope)
    )
    for scope in analysis.scopes:
        if not isinstance(scope.expression, exp.Select):
            continue
        where = scope.expression.args.get("where")
        if where is None:
            continue
        for predicate in _conjuncts(where.this):
            target = _predicate_target(scope, predicate)
            if target is None:
                continue
            alias, child = target
            if (
                uses[id(child)] != 1
                or not isinstance(child.expression, exp.Union)
            ):
                continue
            branches = _positional_union_all_branches(child.expression)
            if branches is None:
                continue
            output_names = _set_output_names(child, branches[0])
            if output_names is None:
                continue
            rewritten: list[tuple[exp.Select, exp.Expression]] = []
            for branch in branches:
                projections = _direct_select_projections(
                    branch,
                    output_names=output_names,
                )
                if projections is None:
                    break
                branch_predicate = _rewrite_predicate(
                    predicate,
                    alias=alias,
                    projections=projections,
                )
                if branch_predicate is None:
                    break
                rewritten.append((branch, branch_predicate))
            else:
                for branch, branch_predicate in rewritten:
                    _append_select_predicate(branch, branch_predicate)


def _push_aggregate_input_predicates(
    analysis: AnalyzedCatalogueQuery,
) -> None:
    uses = Counter(
        id(source)
        for scope in analysis.scopes
        for source in scope.sources.values()
        if isinstance(source, Scope)
    )
    for scope in reversed(analysis.scopes):
        if not isinstance(scope.expression, exp.Select):
            continue
        where = scope.expression.args.get("where")
        if where is None:
            continue
        for predicate in _conjuncts(where.this):
            target = _predicate_target(scope, predicate)
            if target is None:
                continue
            alias, child = target
            if uses[id(child)] != 1:
                continue
            projections = _aggregate_group_projections(child)
            if projections is None:
                continue
            rewritten = _rewrite_predicate(
                predicate,
                alias=alias,
                projections=projections,
            )
            if rewritten is not None:
                _append_scope_predicate(child, rewritten)


def _aggregate_group_projections(
    scope: Scope,
) -> dict[str, exp.Column] | None:
    expression = scope.expression
    if not isinstance(expression, exp.Select):
        return None
    group = expression.args.get("group")
    if (
        not isinstance(group, exp.Group)
        or any(
            value
            for argument, value in group.args.items()
            if argument != "expressions"
        )
        or any(
            expression.args.get(argument) is not None
            for argument in (
                "distinct",
                "limit",
                "offset",
                "qualify",
                "windows",
            )
        )
        or _has_unapproved_scalar_function(expression)
    ):
        return None
    projections = _direct_projections(scope)
    if not projections:
        return None
    grouped: list[exp.Column] = []
    for grouping in group.expressions:
        if isinstance(grouping, exp.Column):
            projected = (
                projections.get(grouping.name.lower())
                if not grouping.table
                else None
            )
            grouped.append(projected or grouping)
            continue
        if (
            isinstance(grouping, exp.Literal)
            and grouping.is_int
            and 1 <= int(grouping.this) <= len(expression.expressions)
        ):
            selection = expression.expressions[int(grouping.this) - 1]
            projected = (
                selection.this
                if isinstance(selection, exp.Alias)
                else selection
            )
            if isinstance(projected, exp.Column):
                grouped.append(projected)
                continue
        return None
    grouped_identities = {_column_identity(column) for column in grouped}
    return {
        output_name: column
        for output_name, column in projections.items()
        if _column_identity(column) in grouped_identities
    }


def _push_having_group_predicates(
    analysis: AnalyzedCatalogueQuery,
) -> None:
    for scope in analysis.scopes:
        if not isinstance(scope.expression, exp.Select):
            continue
        having = scope.expression.args.get("having")
        if having is None:
            continue
        projections = _aggregate_group_projections(scope)
        if projections is None:
            continue
        for predicate in _conjuncts(having.this):
            if not _predicate_is_rewrite_safe(predicate):
                continue
            rewritten = _rewrite_group_predicate(
                predicate,
                projections=projections,
            )
            if rewritten is not None:
                _append_scope_predicate(scope, rewritten)


def _rewrite_group_predicate(
    predicate: exp.Expression,
    *,
    projections: dict[str, exp.Column],
) -> exp.Expression | None:
    replacements: dict[tuple[str, str], exp.Column] = {}
    projected_columns = tuple(projections.values())
    for column in predicate.find_all(exp.Column):
        source = _column_identity(column)
        if column.table:
            if not any(
                source == _column_identity(projected)
                for projected in projected_columns
            ):
                return None
            replacements[source] = column
            continue
        projected = projections.get(column.name.lower())
        if projected is None:
            candidates = {
                _column_identity(candidate): candidate
                for candidate in projected_columns
                if candidate.name.lower() == column.name.lower()
            }
            if len(candidates) != 1:
                return None
            projected = next(iter(candidates.values()))
        replacements[source] = projected
    if not replacements:
        return None
    return predicate.copy().transform(
        lambda node: (
            replacements[_column_identity(node)].copy()
            if isinstance(node, exp.Column)
            else node
        )
    )


def _column_identity(column: exp.Column) -> tuple[str, str]:
    return (column.table.lower(), column.name.lower())


def _push_window_partition_predicates(
    analysis: AnalyzedCatalogueQuery,
) -> None:
    uses = Counter(
        id(source)
        for scope in analysis.scopes
        for source in scope.sources.values()
        if isinstance(source, Scope)
    )
    for scope in reversed(analysis.scopes):
        if not isinstance(scope.expression, exp.Select):
            continue
        where = scope.expression.args.get("where")
        if where is None:
            continue
        for predicate in _conjuncts(where.this):
            target = _predicate_target(scope, predicate)
            if target is None:
                continue
            alias, child = target
            if uses[id(child)] != 1:
                continue
            projections = _window_partition_projections(child)
            if projections is None:
                continue
            rewritten = _rewrite_predicate(
                predicate,
                alias=alias,
                projections=projections,
            )
            if rewritten is not None:
                _append_scope_predicate(child, rewritten)


def _window_partition_projections(
    scope: Scope,
) -> dict[str, exp.Column] | None:
    expression = scope.expression
    if (
        not isinstance(expression, exp.Select)
        or expression.args.get("windows") is not None
        or any(
            expression.args.get(argument) is not None
            for argument in (
                "distinct",
                "group",
                "having",
                "limit",
                "offset",
            )
        )
        or _has_unapproved_scalar_function(expression)
    ):
        return None
    windows = tuple(expression.find_all(exp.Window))
    if not windows:
        return None
    common_partitions: set[tuple[str, str]] | None = None
    for window in windows:
        partitions = window.args.get("partition_by") or ()
        if not partitions or not all(
            isinstance(partition, exp.Column)
            for partition in partitions
        ):
            return None
        identities = {
            _column_identity(partition)
            for partition in partitions
            if isinstance(partition, exp.Column)
        }
        common_partitions = (
            identities
            if common_partitions is None
            else common_partitions & identities
        )
    if not common_partitions:
        return None
    projections = _direct_projections(scope)
    if not projections:
        return None
    return {
        output_name: column
        for output_name, column in projections.items()
        if _column_identity(column) in common_partitions
    }


def _prune_projections(analysis: AnalyzedCatalogueQuery) -> bool:
    references: dict[int, list[tuple[Scope, str]]] = defaultdict(list)
    children: dict[int, Scope] = {}
    for scope in analysis.scopes:
        for alias, (_, source) in scope.selected_sources.items():
            if isinstance(source, Scope):
                references[id(source)].append((scope, alias.lower()))
                children[id(source)] = source

    changed = False
    for child_id, child in children.items():
        required = _required_child_outputs(
            references[child_id],
            child=child,
        )
        if required is None:
            continue
        removable = _removable_projection_indexes(
            child,
            required=required,
        )
        if not removable:
            continue
        selections = child.expression.expressions
        retained = [
            selection
            for index, selection in enumerate(selections)
            if index not in removable
        ]
        if not retained:
            retained = [selections[0]]
        if len(retained) != len(selections):
            child.expression.set("expressions", retained)
            changed = True
    return changed


def _remove_redundant_sorts(
    analysis: AnalyzedCatalogueQuery,
) -> None:
    references: dict[int, list[tuple[Scope, str]]] = defaultdict(list)
    children: dict[int, Scope] = {}
    for scope in analysis.scopes:
        for alias, (_, source) in scope.selected_sources.items():
            if isinstance(source, Scope):
                references[id(source)].append((scope, alias.lower()))
                children[id(source)] = source
    for child_id, child in children.items():
        expression = child.expression
        if (
            not isinstance(expression, exp.Select)
            or expression.args.get("order") is None
            or expression.args.get("limit") is not None
            or expression.args.get("offset") is not None
            or _has_explicit_cte_materialization(expression)
            or not _simple_column_order(expression.args["order"])
        ):
            continue
        consumers = references[child_id]
        if consumers and all(
            _consumer_totally_orders(parent)
            for parent, _ in consumers
        ):
            expression.set("order", None)


def _push_safe_limits(
    analysis: AnalyzedCatalogueQuery,
) -> None:
    uses = Counter(
        id(source)
        for scope in analysis.scopes
        for _, source in scope.selected_sources.values()
        if isinstance(source, Scope)
    )
    for parent in analysis.scopes:
        expression = parent.expression
        if not isinstance(expression, exp.Select):
            continue
        limit = expression.args.get("limit")
        bound = _literal_nonnegative_limit(limit)
        offset = _literal_nonnegative_offset(
            expression.args.get("offset")
        )
        if bound is None or offset is None:
            continue
        if (
            len(parent.selected_sources) != 1
            or expression.args.get("joins")
            or any(
                expression.args.get(argument) is not None
                for argument in (
                    "distinct",
                    "where",
                    "group",
                    "having",
                    "qualify",
                    "order",
                    "windows",
                    "hint",
                )
            )
            or not _direct_row_preserving_projection(expression)
        ):
            continue
        _, (_, child) = next(iter(parent.selected_sources.items()))
        if (
            not isinstance(child, Scope)
            or uses[id(child)] != 1
            or not isinstance(child.expression, exp.Select)
            or not _consumer_totally_orders(child)
            or child.expression.args.get("limit") is not None
            or child.expression.args.get("offset") is not None
            or _has_explicit_cte_materialization(child.expression)
        ):
            continue
        child.expression.set(
            "limit",
            exp.Limit(
                expression=exp.Literal.number(bound + offset),
            ),
        )


def _push_top_k(
    analysis: AnalyzedCatalogueQuery,
) -> None:
    uses = Counter(
        id(source)
        for scope in analysis.scopes
        for _, source in scope.selected_sources.values()
        if isinstance(source, Scope)
    )
    for parent in analysis.scopes:
        expression = parent.expression
        if not isinstance(expression, exp.Select):
            continue
        bound = _literal_nonnegative_limit(expression.args.get("limit"))
        offset = _literal_nonnegative_offset(
            expression.args.get("offset")
        )
        if (
            bound is None
            or offset is None
            or not _consumer_totally_orders(parent)
            or len(parent.selected_sources) != 1
            or expression.args.get("joins")
            or any(
                expression.args.get(argument) is not None
                for argument in (
                    "distinct",
                    "where",
                    "group",
                    "having",
                    "qualify",
                    "windows",
                    "hint",
                )
            )
        ):
            continue
        _, (_, child) = next(iter(parent.selected_sources.items()))
        if (
            not isinstance(child, Scope)
            or uses[id(child)] != 1
            or not isinstance(child.expression, exp.Select)
            or child.outer_columns
            or child.expression.args.get("limit") is not None
            or child.expression.args.get("offset") is not None
            or _has_explicit_cte_materialization(child.expression)
            or (
                child.expression.args.get("order") is not None
                and not _simple_column_order(
                    child.expression.args["order"]
                )
            )
        ):
            continue
        child_projections = _direct_projections(child)
        if (
            child_projections is None
            or len(child_projections) != len(
                child.expression.expressions
            )
        ):
            continue
        rewritten_order = _rewrite_order_for_child(
            expression,
            child_outputs=frozenset(child_projections),
        )
        if rewritten_order is None:
            continue
        child.expression.set("order", rewritten_order)
        child.expression.set(
            "limit",
            exp.Limit(
                expression=exp.Literal.number(bound + offset),
            ),
        )


def _rewrite_order_for_child(
    expression: exp.Select,
    *,
    child_outputs: frozenset[str],
) -> exp.Order | None:
    selections: list[tuple[str, exp.Column]] = []
    identities: dict[tuple[str, str], int] = {}
    for index, selection in enumerate(expression.expressions):
        projected = (
            selection.this if isinstance(selection, exp.Alias) else selection
        )
        if not isinstance(projected, exp.Column):
            return None
        selections.append((selection.alias_or_name.lower(), projected))
        identities[_column_identity(projected)] = index
    rewritten: list[exp.Expression] = []
    for ordered in expression.args["order"].expressions:
        ordering = (
            ordered.this if isinstance(ordered, exp.Ordered) else ordered
        )
        index: int | None = None
        if isinstance(ordering, exp.Literal) and ordering.is_int:
            candidate = int(ordering.this) - 1
            if 0 <= candidate < len(selections):
                index = candidate
        elif isinstance(ordering, exp.Column):
            if not ordering.table:
                aliases = [
                    candidate
                    for candidate, (output_name, _) in enumerate(selections)
                    if output_name == ordering.name.lower()
                ]
                if len(aliases) == 1:
                    index = aliases[0]
            if index is None:
                index = identities.get(_column_identity(ordering))
        if index is None:
            return None
        child_name = selections[index][1].name.lower()
        if child_name not in child_outputs:
            return None
        rewritten_ordering = ordered.copy()
        if isinstance(rewritten_ordering, exp.Ordered):
            rewritten_ordering.set("this", exp.column(child_name))
        else:
            rewritten_ordering = exp.column(child_name)
        rewritten.append(rewritten_ordering)
    return exp.Order(expressions=rewritten)


def _literal_nonnegative_limit(limit: exp.Expression | None) -> int | None:
    if (
        not isinstance(limit, exp.Limit)
        or limit.args.get("limit_options") is not None
    ):
        return None
    value = limit.expression
    if (
        not isinstance(value, exp.Literal)
        or not value.is_int
        or int(value.this) < 0
    ):
        return None
    return int(value.this)


def _literal_nonnegative_offset(
    offset: exp.Expression | None,
) -> int | None:
    if offset is None:
        return 0
    if not isinstance(offset, exp.Offset):
        return None
    value = offset.expression
    if (
        not isinstance(value, exp.Literal)
        or not value.is_int
        or int(value.this) < 0
    ):
        return None
    return int(value.this)


def _direct_row_preserving_projection(expression: exp.Select) -> bool:
    return bool(expression.expressions) and all(
        isinstance(
            selection.this if isinstance(selection, exp.Alias) else selection,
            exp.Column,
        )
        and not (
            selection.this if isinstance(selection, exp.Alias) else selection
        ).is_star
        for selection in expression.expressions
    )


def _has_explicit_cte_materialization(expression: exp.Select) -> bool:
    parent = expression.parent
    return (
        isinstance(parent, exp.CTE)
        and parent.args.get("materialized") is not None
    )


def _simple_column_order(order: exp.Order) -> bool:
    for ordered in order.expressions:
        ordering = (
            ordered.this if isinstance(ordered, exp.Ordered) else ordered
        )
        if isinstance(ordering, exp.Column):
            continue
        if isinstance(ordering, exp.Literal) and ordering.is_int:
            continue
        return False
    return True


def _consumer_totally_orders(scope: Scope) -> bool:
    expression = scope.expression
    if (
        not isinstance(expression, exp.Select)
        or expression.args.get("order") is None
        or expression.args.get("distinct") is not None
        or any(
            expression.args.get(argument) is not None
            for argument in ("group", "having", "qualify", "windows")
        )
        or next(
            iter(_local_expressions(expression, exp.Window)),
            None,
        )
        is not None
        or next(
            iter(_local_expressions(expression, exp.AggFunc)),
            None,
        )
        is not None
        or _has_unapproved_scalar_function(expression)
    ):
        return False
    output_names: list[str] = []
    projected_columns: dict[tuple[str, str], str] = {}
    for selection in expression.expressions:
        projected = (
            selection.this if isinstance(selection, exp.Alias) else selection
        )
        if not isinstance(projected, exp.Column) or projected.is_star:
            return False
        output_name = selection.alias_or_name.lower()
        if not output_name:
            return False
        output_names.append(output_name)
        projected_columns[_column_identity(projected)] = output_name
    if len(output_names) != len(set(output_names)):
        return False
    ordered_outputs: set[str] = set()
    for ordered in expression.args["order"].expressions:
        ordering = (
            ordered.this if isinstance(ordered, exp.Ordered) else ordered
        )
        if isinstance(ordering, exp.Literal) and ordering.is_int:
            index = int(ordering.this) - 1
            if not 0 <= index < len(output_names):
                return False
            ordered_outputs.add(output_names[index])
            continue
        if not isinstance(ordering, exp.Column):
            return False
        by_alias = ordering.name.lower()
        if not ordering.table and by_alias in output_names:
            ordered_outputs.add(by_alias)
            continue
        output_name = projected_columns.get(_column_identity(ordering))
        if output_name is None:
            return False
        ordered_outputs.add(output_name)
    return ordered_outputs == set(output_names)


def _required_child_outputs(
    references: list[tuple[Scope, str]],
    *,
    child: Scope,
) -> set[str] | None:
    required: set[str] = set()
    for parent, alias in references:
        selected_scope_aliases = {
            selected_alias.lower()
            for selected_alias, (_, source) in parent.selected_sources.items()
            if isinstance(source, Scope)
        }
        for selection in parent.expression.expressions:
            projected = (
                selection.this
                if isinstance(selection, exp.Alias)
                else selection
            )
            if isinstance(projected, exp.Star):
                return None
            if (
                isinstance(projected, exp.Column)
                and projected.is_star
                and (
                    not projected.table
                    or projected.table.lower() == alias
                )
            ):
                return None
        for column in _local_expressions(parent.expression, exp.Column):
            if column.table:
                if column.table.lower() == alias:
                    required.add(column.name.lower())
                continue
            if len(selected_scope_aliases) != 1:
                return None
            if alias in selected_scope_aliases:
                required.add(column.name.lower())
        required.update(_using_columns_for_alias(parent, alias))

    output_names = {
        selection.alias_or_name.lower()
        for selection in child.expression.expressions
        if selection.alias_or_name
    }
    for argument in ("where", "group", "having", "qualify", "order"):
        clause = child.expression.args.get(argument)
        if clause is None:
            continue
        for column in clause.find_all(exp.Column):
            if not column.table and column.name.lower() in output_names:
                required.add(column.name.lower())
    return required


def _local_expressions(
    expression: exp.Expression,
    expression_type: type[exp.Expression],
) -> tuple[exp.Expression, ...]:
    found: list[exp.Expression] = []

    def visit(node: exp.Expression, *, root: bool = False) -> None:
        if not root and isinstance(node, exp.Query):
            return
        if isinstance(node, expression_type):
            found.append(node)
        for child in node.iter_expressions():
            visit(child)

    visit(expression, root=True)
    return tuple(found)


def _using_columns_for_alias(scope: Scope, alias: str) -> set[str]:
    expression = scope.expression
    if not isinstance(expression, exp.Select):
        return set()
    from_clause = expression.args.get("from_")
    available: list[str] = []
    if from_clause is not None and from_clause.this is not None:
        available.append(from_clause.this.alias_or_name.lower())
    required: set[str] = set()
    for join in expression.args.get("joins") or ():
        right_alias = join.this.alias_or_name.lower()
        using = {
            identifier.name.lower()
            for identifier in join.args.get("using") or ()
            if isinstance(identifier, exp.Identifier)
        }
        if alias == right_alias or alias in available:
            required.update(using)
        available.append(right_alias)
    return required


def _removable_projection_indexes(
    scope: Scope,
    *,
    required: set[str],
) -> set[int]:
    expression = scope.expression
    if (
        not isinstance(expression, exp.Select)
        or scope.outer_columns
        or expression.args.get("distinct") is not None
        or _has_unapproved_scalar_function(expression)
        or _has_projection_ordinal(expression)
    ):
        return set()
    output_names = [
        selection.alias_or_name.lower()
        for selection in expression.expressions
    ]
    if (
        any(not output_name for output_name in output_names)
        or len(output_names) != len(set(output_names))
        or any(
            isinstance(selection, exp.Star)
            or (
                isinstance(selection, exp.Column)
                and isinstance(selection.this, exp.Star)
            )
            for selection in expression.expressions
        )
    ):
        return set()
    removable: set[int] = set()
    for index, (output_name, selection) in enumerate(
        zip(output_names, expression.expressions, strict=True)
    ):
        projected = (
            selection.this if isinstance(selection, exp.Alias) else selection
        )
        if output_name not in required and isinstance(projected, exp.Column):
            removable.add(index)
    return removable


def _has_projection_ordinal(expression: exp.Select) -> bool:
    for argument in ("group", "order"):
        clause = expression.args.get(argument)
        if clause is None:
            continue
        if any(
            isinstance(literal, exp.Literal) and literal.is_int
            for literal in clause.find_all(exp.Literal)
        ):
            return True
    return False


def _positional_union_all_branches(
    expression: exp.Expression,
) -> tuple[exp.Select, ...] | None:
    if isinstance(expression, exp.Select):
        if not _safe_pushdown_select(expression):
            return None
        return (expression,)
    if not isinstance(expression, exp.Union):
        return None
    if (
        expression.args.get("distinct") is not False
        or expression.args.get("by_name")
        or any(
            expression.args.get(argument) is not None
            for argument in ("limit", "offset", "order")
        )
    ):
        return None
    left = _positional_union_all_branches(expression.this)
    right = _positional_union_all_branches(expression.expression)
    if left is None or right is None:
        return None
    return (*left, *right)


def _set_output_names(
    scope: Scope,
    first_branch: exp.Select,
) -> tuple[str, ...] | None:
    names = tuple(column.lower() for column in scope.outer_columns)
    if not names:
        names = tuple(
            selection.alias_or_name.lower()
            for selection in first_branch.expressions
        )
    if not names or len(names) != len(set(names)):
        return None
    return names


def _direct_select_projections(
    expression: exp.Select,
    *,
    output_names: tuple[str, ...],
) -> dict[str, exp.Column] | None:
    if len(output_names) != len(expression.expressions):
        return None
    result: dict[str, exp.Column] = {}
    for output_name, selection in zip(
        output_names,
        expression.expressions,
        strict=True,
    ):
        projected = (
            selection.this if isinstance(selection, exp.Alias) else selection
        )
        if not isinstance(projected, exp.Column):
            return None
        result[output_name] = projected
    return result


def _append_select_predicate(
    expression: exp.Select,
    predicate: exp.Expression,
) -> None:
    branch_where = expression.args.get("where")
    combined = (
        exp.and_(branch_where.this.copy(), predicate)
        if branch_where is not None
        else predicate
    )
    expression.set("where", exp.Where(this=combined))


def _append_scope_predicate(
    scope: Scope,
    predicate: exp.Expression,
) -> None:
    child_where = scope.expression.args.get("where")
    combined = (
        exp.and_(child_where.this.copy(), predicate)
        if child_where is not None
        else predicate
    )
    scope.expression.set("where", exp.Where(this=combined))


def _predicate_target(
    scope: Scope,
    predicate: exp.Expression,
) -> tuple[str, Scope] | None:
    if not _predicate_is_rewrite_safe(predicate):
        return None
    columns = tuple(predicate.find_all(exp.Column))
    if not columns:
        return None
    aliases = {column.table.lower() for column in columns if column.table}
    if not aliases:
        if len(scope.selected_sources) != 1:
            return None
        aliases = {next(iter(scope.selected_sources)).lower()}
    if len(aliases) != 1:
        return None
    alias = next(iter(aliases))
    if any(column.table and column.table.lower() != alias for column in columns):
        return None
    source = scope.sources.get(alias)
    return (alias, source) if isinstance(source, Scope) else None


def _safe_pushdown_scope(scope: Scope) -> bool:
    return (
        isinstance(scope.expression, exp.Select)
        and _safe_pushdown_select(scope.expression)
    )


def _safe_pushdown_select(expression: exp.Select) -> bool:
    return (
        not any(
            expression.args.get(argument) is not None
            for argument in _PUSHDOWN_BARRIERS
        )
        and next(expression.find_all(exp.Window), None) is None
        and not _has_unapproved_scalar_function(expression)
    )


def _direct_projections(scope: Scope) -> dict[str, exp.Column] | None:
    result: dict[str, exp.Column] = {}
    outer_names = tuple(column.lower() for column in scope.outer_columns)
    if outer_names and len(outer_names) != len(scope.expression.expressions):
        return None
    for index, selection in enumerate(scope.expression.expressions):
        expression = (
            selection.this if isinstance(selection, exp.Alias) else selection
        )
        if not isinstance(expression, exp.Column):
            continue
        output_name = (
            outer_names[index]
            if outer_names
            else selection.alias_or_name.lower()
        )
        if output_name in result:
            return None
        result[output_name] = expression
    return result


def _rewrite_predicate(
    predicate: exp.Expression,
    *,
    alias: str,
    projections: dict[str, exp.Column],
) -> exp.Expression | None:
    for column in predicate.find_all(exp.Column):
        if column.table and column.table.lower() != alias:
            return None
        if column.name.lower() not in projections:
            return None

    def replace(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column):
            return projections[node.name.lower()].copy()
        return node

    return predicate.copy().transform(replace)


def _conjuncts(expression: exp.Expression) -> tuple[exp.Expression, ...]:
    if isinstance(expression, exp.And):
        return (*_conjuncts(expression.this), *_conjuncts(expression.expression))
    return (expression,)


def _predicate_is_rewrite_safe(predicate: exp.Expression) -> bool:
    if next(predicate.find_all((exp.Query, exp.Window)), None) is not None:
        return False
    for function in predicate.find_all(exp.Func):
        if (
            isinstance(
                function,
                (
                    *_VOLATILE_FUNCTION_TYPES,
                    exp.AggFunc,
                    exp.Anonymous,
                    exp.Explode,
                    exp.Unnest,
                ),
            )
            or function.name.lower() in _VOLATILE_FUNCTION_NAMES
            or not isinstance(
                function,
                _DETERMINISTIC_ROW_LOCAL_FUNCTION_TYPES,
            )
        ):
            return False
    return True


def _has_unapproved_scalar_function(expression: exp.Expression) -> bool:
    for function in expression.find_all(exp.Func):
        if (
            isinstance(function, exp.AggFunc)
            or isinstance(function.parent, exp.Window)
        ):
            continue
        if (
            isinstance(function, _VOLATILE_FUNCTION_TYPES)
            or function.name.lower() in _VOLATILE_FUNCTION_NAMES
            or isinstance(
                function,
                (exp.Anonymous, exp.Explode, exp.Unnest),
            )
            or not isinstance(
                function,
                _DETERMINISTIC_ROW_LOCAL_FUNCTION_TYPES,
            )
        ):
            return True
    return False
