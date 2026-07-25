"""Catalogue-wide, purpose-aware SQL optimization compiler."""

from __future__ import annotations

import re

from sqlglot import exp
from sqlglot.optimizer.scope import Scope

from .analysis import (
    AnalyzedCatalogueQuery,
    analyze_catalogue_query,
    analyze_resolved_query,
)
from .errors import (
    OptimizationCode,
    OptimizationDiagnostic,
    QueryOptimizationUnavailable,
)
from .function_safety import (
    DETERMINISTIC_DUCKDB_FUNCTION_NAMES,
    DETERMINISTIC_ROW_LOCAL_FUNCTION_TYPES,
    VOLATILE_FUNCTION_NAMES,
    VOLATILE_FUNCTION_TYPES,
)
from .interactive import (
    InteractiveCompilation,
    compile_interactive_query_with_explanation,
)
from .plan import (
    CatalogueQueryPlan,
    CatalogueScan,
    RelationKeyBinding,
)
from .relational import ColumnLineage, ColumnNode, ScopeRelationalFacts
from .purpose import (
    FullMaterializationPurpose,
    InteractiveQueryPurpose,
    KeyedMaterializationPurpose,
)
from .rewrite import render_keyed_plan

_COMPILER_VERSION = 30
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_UNSUPPORTED_SELECT_ARGS = (
    "laterals",
    "limit",
    "offset",
)
_SELECT_ARG_LABELS = {"with_": "common table expressions"}
_RESERVED_RELATIONS = frozenset(
    {
        "_atlas_materialization_changed_keys",
        "_atlas_materialization_changes",
    }
)
_DYNAMIC_RELATION_FUNCTIONS = frozenset({"query", "query_table"})
_EXTERNAL_RELATION_FUNCTIONS = frozenset(
    {
        "csv_scan",
        "delta_scan",
        "glob",
        "iceberg_scan",
        "json_scan",
        "mysql_scan",
        "parquet_scan",
        "postgres_scan",
        "sqlite_scan",
        "st_read",
    }
)
_DETERMINISTIC_FUNCTION_TYPES = (
    *DETERMINISTIC_ROW_LOCAL_FUNCTION_TYPES,
    exp.Explode,
    exp.Exists,
    exp.Unnest,
)
_KEYED_AGGREGATE_TYPES = (
    exp.Avg,
    exp.BitwiseAndAgg,
    exp.BitwiseOrAgg,
    exp.BitwiseXorAgg,
    exp.Count,
    exp.CountIf,
    exp.Corr,
    exp.CovarPop,
    exp.CovarSamp,
    exp.LogicalAnd,
    exp.LogicalOr,
    exp.Max,
    exp.Median,
    exp.Min,
    exp.StddevPop,
    exp.StddevSamp,
    exp.Sum,
    exp.Variance,
    exp.VariancePop,
)
_VALUE_ORDERED_AGGREGATE_TYPES = (
    exp.AnyValue,
    exp.ArrayAgg,
    exp.First,
    exp.GroupConcat,
    exp.Last,
)
_ARG_EXTREME_AGGREGATE_TYPES = (exp.ArgMax, exp.ArgMin)
_KEY_LOCAL_WINDOW_FUNCTION_TYPES = (
    *_KEYED_AGGREGATE_TYPES,
    exp.CumeDist,
    exp.DenseRank,
    exp.PercentRank,
    exp.Rank,
)
_ROW_CHOICE_WINDOW_FUNCTION_TYPES = (
    exp.FirstValue,
    exp.Lag,
    exp.LastValue,
    exp.Lead,
    exp.NthValue,
    exp.Ntile,
    exp.RowNumber,
)


def compile_catalogue_query(
    sql: str,
    *,
    purpose: (
        FullMaterializationPurpose
        | KeyedMaterializationPurpose
        | InteractiveQueryPurpose
    ),
) -> str:
    """Return equivalent optimized SQL or raise so callers can run `sql`."""

    if not isinstance(
        purpose,
        (
            FullMaterializationPurpose,
            KeyedMaterializationPurpose,
            InteractiveQueryPurpose,
        ),
    ):
        _unavailable(
            OptimizationCode.UNSUPPORTED_PURPOSE,
            f"Unsupported catalogue compilation purpose "
            f"{type(purpose).__name__}.",
            documentation_anchor="query-boundary",
        )
    definitions = _definition_inputs(purpose)
    analysis = analyze_catalogue_query(
        sql,
        **definitions,
        bound_scalar_inputs=isinstance(
            purpose,
            InteractiveQueryPurpose,
        ),
    )
    if isinstance(purpose, KeyedMaterializationPurpose):
        analysis = _resolve_keyed_named_windows(analysis)
    query = analysis.query
    if isinstance(purpose, FullMaterializationPurpose):
        return analysis.normalized_sql
    if isinstance(purpose, InteractiveQueryPurpose):
        return compile_interactive_query_with_explanation(analysis).sql
    if isinstance(query, exp.SetOperation):
        return _compile_set_operation(query, purpose)
    plan = _compile_keyed_plan(analysis, purpose)
    return render_keyed_plan(
        plan,
        changed_keys_relation=purpose.changed_keys_relation,
        key_rows=purpose.key_rows,
    )


def compile_interactive_catalogue_query(
    sql: str,
    *,
    purpose: InteractiveQueryPurpose,
) -> InteractiveCompilation:
    """Compile interactive SQL and explain only rules that changed the query."""

    definitions = _definition_inputs(purpose)
    analysis = analyze_catalogue_query(
        sql,
        **definitions,
        bound_scalar_inputs=True,
    )
    return compile_interactive_query_with_explanation(analysis)


def _definition_inputs(purpose) -> dict[str, tuple]:
    snapshot = purpose.metadata
    if snapshot is None:
        return {
            "scalar_macros": purpose.scalar_macros,
            "table_macros": purpose.table_macros,
            "views": purpose.views,
            "scalar_functions": purpose.scalar_functions,
        }
    if any(
        (
            purpose.scalar_macros,
            purpose.table_macros,
            purpose.views,
            purpose.scalar_functions,
        )
    ):
        raise ValueError(
            "Catalogue metadata snapshots cannot be mixed with independent "
            "definition inputs."
        )
    return {
        "scalar_macros": snapshot.scalar_macros,
        "table_macros": snapshot.table_macros,
        "views": snapshot.views,
        "scalar_functions": snapshot.scalar_functions,
    }


def _resolve_keyed_named_windows(
    analysis: AnalyzedCatalogueQuery,
) -> AnalyzedCatalogueQuery:
    """Inline named windows so keyed proofs see their effective clauses."""

    if not any(
        select.args.get("windows")
        for select in analysis.query.find_all(exp.Select)
    ):
        return analysis
    query = analysis.query.copy()
    for select in query.find_all(exp.Select):
        definitions = tuple(select.args.get("windows") or ())
        if not definitions:
            continue
        by_name: dict[str, exp.Window] = {}
        for definition in definitions:
            name = definition.name.lower()
            if not name or name in by_name:
                _unavailable(
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                    "Named windows in one scope must have unique names.",
                    sql_fragment=definition.sql(dialect="duckdb"),
                    documentation_anchor="key-local-windows",
                )
            by_name[name] = definition

        resolved: dict[str, exp.Window] = {}
        resolving: set[str] = set()

        def resolve(name: str) -> exp.Window:
            if name in resolved:
                return resolved[name]
            definition = by_name.get(name)
            if definition is None:
                _unavailable(
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                    f"Named window {name!r} is not defined in this scope.",
                    sql_fragment=name,
                    documentation_anchor="key-local-windows",
                )
            if name in resolving:
                _unavailable(
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                    "Named window inheritance must be acyclic.",
                    sql_fragment=definition.sql(dialect="duckdb"),
                    documentation_anchor="key-local-windows",
                )
            resolving.add(name)
            inherited = None
            if definition.alias:
                inherited = resolve(definition.alias.lower())
            effective = _merge_window_clauses(
                inherited,
                definition,
                sql_fragment=definition.sql(dialect="duckdb"),
            )
            resolving.remove(name)
            resolved[name] = effective
            return effective

        for name in by_name:
            resolve(name)

        definition_ids = {id(definition) for definition in definitions}
        usages = tuple(
            window
            for window in select.find_all(exp.Window)
            if id(window) not in definition_ids
            and window.find_ancestor(exp.Select) is select
        )
        for window in usages:
            if not window.alias:
                continue
            inherited = resolve(window.alias.lower())
            effective = _merge_window_clauses(
                inherited,
                window,
                sql_fragment=window.sql(dialect="duckdb"),
            )
            window.set(
                "partition_by",
                [item.copy() for item in effective.args["partition_by"]],
            )
            window.set(
                "order",
                effective.args.get("order").copy()
                if effective.args.get("order") is not None
                else None,
            )
            window.set(
                "spec",
                effective.args.get("spec").copy()
                if effective.args.get("spec") is not None
                else None,
            )
            window.set("alias", None)
        select.set("windows", None)
    return analyze_resolved_query(query)


def _merge_window_clauses(
    inherited: exp.Window | None,
    extension: exp.Window,
    *,
    sql_fragment: str,
) -> exp.Window:
    effective = extension.copy()
    if inherited is None:
        return effective
    for argument in ("partition_by", "order", "spec"):
        parent_value = inherited.args.get(argument)
        child_value = extension.args.get(argument)
        if parent_value and child_value:
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "Named window inheritance cannot override an existing "
                f"{argument.replace('_', ' ')} clause.",
                sql_fragment=sql_fragment,
                documentation_anchor="key-local-windows",
            )
        if not child_value and parent_value:
            if isinstance(parent_value, list):
                effective.set(
                    argument,
                    [item.copy() for item in parent_value],
                )
            else:
                effective.set(argument, parent_value.copy())
    return effective


def _compile_set_operation(
    query: exp.SetOperation,
    purpose: KeyedMaterializationPurpose,
) -> str:
    if (
        query.args.get("limit") is not None
        or query.args.get("offset") is not None
    ):
        _unavailable(
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            "Set operations with global LIMIT or OFFSET modifiers are not "
            "optimized.",
            sql_fragment=query.sql(dialect="duckdb"),
            documentation_anchor="set-operations",
        )
    with_clause = query.args.get("with_")
    if (
        isinstance(with_clause, exp.With)
        and with_clause.args.get("recursive")
    ):
        _unavailable(
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            "Recursive set-root common table expressions are not optimized.",
            sql_fragment=with_clause.sql(dialect="duckdb"),
            documentation_anchor="set-operations",
        )
    if not query.args.get("by_name"):
        _validate_positional_set_key_alignment(query, purpose.key_columns)
    compiled = query.copy()
    compiled.set("with_", None)
    for argument in ("this", "expression"):
        branch = query.args[argument]
        if not isinstance(branch, exp.Query):
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "Every set-operation branch must be a query.",
                sql_fragment=branch.sql(dialect="duckdb"),
                documentation_anchor="set-operations",
            )
        if isinstance(with_clause, exp.With):
            branch = _branch_with_required_ctes(branch, with_clause)
        branch_sql = compile_catalogue_query(
            branch.sql(dialect="duckdb"),
            purpose=purpose,
        )
        compiled_branch = exp.maybe_parse(branch_sql, dialect="duckdb")
        if compiled_branch.args.get("with_") is not None:
            compiled_branch = exp.Subquery(this=compiled_branch)
        compiled.set(argument, compiled_branch)
    return compiled.sql(dialect="duckdb", pretty=True)


def _branch_with_required_ctes(
    branch: exp.Query,
    with_clause: exp.With,
) -> exp.Query:
    definitions = tuple(with_clause.expressions)
    names = tuple(cte.alias_or_name.lower() for cte in definitions)
    if not all(names) or len(set(names)) != len(names):
        _unavailable(
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            "Set-root CTE names must be unique.",
            sql_fragment=with_clause.sql(dialect="duckdb"),
            documentation_anchor="set-operations",
        )
    positions = {name: index for index, name in enumerate(names)}
    shadowed = {
        cte.alias_or_name.lower()
        for nested in branch.find_all(exp.With)
        for cte in nested.expressions
    } & set(names)
    if shadowed:
        _unavailable(
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            "Nested CTEs cannot shadow set-root definitions.",
            sql_fragment=", ".join(sorted(shadowed)),
            documentation_anchor="set-operations",
        )
    required = set(_referenced_cte_names(branch, set(names)))
    pending = list(required)
    while pending:
        name = pending.pop()
        position = positions[name]
        available = set(names[:position])
        dependencies = set(
            _referenced_cte_names(definitions[position].this, available)
        )
        new_dependencies = dependencies - required
        required.update(new_dependencies)
        pending.extend(new_dependencies)
    scoped = branch.copy()
    if required:
        scoped.set(
            "with_",
            exp.With(
                expressions=[
                    definition.copy()
                    for name, definition in zip(
                        names,
                        definitions,
                        strict=True,
                    )
                    if name in required
                ]
            ),
        )
    return scoped


def _referenced_cte_names(
    expression: exp.Expression,
    available: set[str],
) -> tuple[str, ...]:
    return tuple(
        table.name.lower()
        for table in expression.find_all(exp.Table)
        if isinstance(table.this, exp.Identifier)
        and not table.db
        and not table.catalog
        and table.name.lower() in available
    )


def _validate_positional_set_key_alignment(
    query: exp.SetOperation,
    keys: tuple[str, ...],
) -> None:
    left_names = _set_output_names(query.this)
    right_names = _set_output_names(query.expression)
    if left_names is None or right_names is None:
        _unavailable(
            OptimizationCode.UNSUPPORTED_PROJECTION,
            "Positional set operations with wildcard outputs require source "
            "schema metadata.",
            sql_fragment=query.sql(dialect="duckdb"),
            documentation_anchor="set-operations",
        )
    for key in keys:
        normalized_key = key.lower()
        if normalized_key not in left_names or normalized_key not in right_names:
            continue
        if left_names.index(normalized_key) != right_names.index(normalized_key):
            _unavailable(
                OptimizationCode.KEY_NOT_PRESERVED,
                f"Positional set-operation branches place stable key "
                f"{key!r} at different output positions.",
                sql_fragment=query.sql(dialect="duckdb"),
                documentation_anchor="set-operations",
            )


def _set_output_names(query: exp.Expression) -> tuple[str, ...] | None:
    if isinstance(query, exp.Subquery):
        return _set_output_names(query.this)
    if isinstance(query, exp.Select):
        names = tuple(
            expression.alias_or_name.lower()
            for expression in query.expressions
            if expression.alias_or_name
        )
        if len(names) != len(query.expressions):
            return None
        return names
    if not isinstance(query, exp.SetOperation):
        return None
    left_names = _set_output_names(query.this)
    if left_names is None or not query.args.get("by_name"):
        return left_names
    right_names = _set_output_names(query.expression)
    if right_names is None:
        return None
    return left_names + tuple(
        name for name in right_names if name not in left_names
    )


def _compile_keyed_plan(
    analysis: AnalyzedCatalogueQuery,
    purpose: KeyedMaterializationPurpose,
) -> CatalogueQueryPlan:
    query = analysis.query
    source_table = _identifier(purpose.source_table, label="Source table")
    if source_table.lower() in _RESERVED_RELATIONS:
        _unavailable(
            OptimizationCode.RESERVED_RELATION,
            f"Relation {source_table!r} is reserved for catalogue execution.",
            sql_fragment=source_table,
            documentation_anchor="query-boundary",
        )
    keys = tuple(
        _identifier(column, label="Key column")
        for column in purpose.key_columns
    )
    if not keys:
        _unavailable(
            OptimizationCode.KEY_REQUIRED,
            "Keyed optimization requires at least one stable key column.",
            documentation_anchor="stable-output-key",
        )
    if len(set(column.lower() for column in keys)) != len(keys):
        _unavailable(
            OptimizationCode.KEY_REQUIRED,
            "Stable key columns must be unique.",
            documentation_anchor="stable-output-key",
        )

    if not isinstance(query, exp.Select):
        _unavailable(
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            f"{type(query).__name__} queries are not optimized yet.",
            sql_fragment=query.sql(dialect="duckdb"),
            documentation_anchor="supported-subset",
        )
    unsupported = next(
        (
            name
            for name in _UNSUPPORTED_SELECT_ARGS
            if query.args.get(name) is not None
        ),
        None,
    )
    if unsupported is not None:
        node = query.args[unsupported]
        label = _SELECT_ARG_LABELS.get(
            unsupported, unsupported.replace("_", " ")
        )
        _unavailable(
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            f"Queries containing {label} are not optimized yet.",
            sql_fragment=_sql_fragment(node),
            documentation_anchor="supported-subset",
        )
    with_clause = query.args.get("with_")
    if with_clause is not None and with_clause.args.get("recursive"):
        _unavailable(
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            "Recursive common table expressions are not optimized.",
            sql_fragment=with_clause.sql(dialect="duckdb"),
            documentation_anchor="supported-subset",
        )
    _validate_managed_relations(query)
    _validate_functions(query)

    scopes = analysis.scopes
    if not scopes or any(
        not isinstance(
            scope.expression,
            (exp.Select, exp.SetOperation, exp.Unnest, exp.Lateral),
        )
        for scope in scopes
    ):
        _unavailable(
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            "Only SELECT-based non-recursive scopes are optimized currently.",
            sql_fragment=query.sql(dialect="duckdb"),
            documentation_anchor="supported-subset",
        )
    lineage = ColumnLineage(analysis.column_lineage.components)
    output_names = {
        id(scope): _scope_output_names(scope, keys=keys) for scope in scopes
    }
    using_columns: dict[int, set[str]] = {
        id(scope): set() for scope in scopes
    }
    physical: list[tuple[Scope, str, exp.Table]] = []
    for scope in scopes:
        if not isinstance(scope.expression, exp.Select):
            if isinstance(scope.expression, exp.SetOperation):
                continue
            _validate_expansion_scope(scope)
            continue
        _validate_scope_shape(scope)
        _add_scope_join_lineage(
            scope,
            keys=keys,
            lineage=lineage,
            output_names=output_names,
            using_columns=using_columns[id(scope)],
            relational_facts=analysis.facts_for(scope),
        )
        facts = analysis.facts_for(scope)
        _validate_full_join_key_projection(
            scope,
            keys=keys,
            is_root=scope is scopes[-1],
        )
        _validate_scope_projections(
            scope,
            keys=keys,
            lineage=lineage,
            output_names=output_names,
            using_columns=using_columns[id(scope)],
        )
        _add_explicit_output_lineage(scope, lineage=lineage)
        aliases: set[str] = set()
        for alias, (_, selected) in scope.selected_sources.items():
            normalized_alias = alias.lower()
            if normalized_alias in aliases:
                _unavailable(
                    OptimizationCode.UNSUPPORTED_RELATION,
                    "Every relation in a scope must have a unique alias.",
                    documentation_anchor="supported-subset",
                )
            aliases.add(normalized_alias)
            if isinstance(selected, exp.Table):
                _validate_physical_table(selected)
                physical.append((scope, normalized_alias, selected))
    matching = tuple(
        item
        for item in physical
        if item[2].name.lower() == source_table.lower()
    )
    if len(matching) != 1:
        _unavailable(
            OptimizationCode.SOURCE_NOT_READ,
            f"The query must read driving table {source_table!r} exactly once.",
            documentation_anchor="driving-table",
        )
    source_scope, source_alias, source_relation = matching[0]
    if not _has_preserved_driver_path(
        root_scope=scopes[-1],
        source_scope=source_scope,
        source_alias=source_alias,
        analysis=analysis,
    ):
        _unavailable(
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            "The driving table cannot be on the nullable side of an outer join.",
            sql_fragment=source_relation.sql(dialect="duckdb"),
            documentation_anchor="join-lineage",
        )
    source_nodes = {
        key.lower(): _boundary_node(
            source_scope,
            source_alias,
            key,
            lineage=lineage,
            output_names=output_names,
        )
        for key in keys
    }
    _validate_arg_extreme_aggregates(
        scopes,
        lineage=lineage,
        source_nodes=source_nodes,
        keys=keys,
        output_names=output_names,
    )
    _validate_keyed_grouping(
        scopes,
        lineage=lineage,
        source_nodes=source_nodes,
        keys=keys,
        output_names=output_names,
    )
    _validate_key_local_windows(
        scopes,
        lineage=lineage,
        source_nodes=source_nodes,
        keys=keys,
        output_names=output_names,
    )
    _validate_key_local_distinct_on(
        scopes,
        lineage=lineage,
        source_nodes=source_nodes,
        keys=keys,
        output_names=output_names,
    )
    scans: list[CatalogueScan] = []
    for ordinal, (scope, alias, table) in enumerate(physical):
        bindings: list[RelationKeyBinding] = []
        anchored_dependency = _is_materialized_driver_dependency(
            scope=scope,
            alias=alias,
            source_scope=source_scope,
            source_alias=source_alias,
            lineage=lineage,
        )
        for key in keys:
            candidates = sorted(
                column
                for scope_id, candidate_alias, column in lineage.nodes
                if scope_id == id(scope)
                and candidate_alias == alias
                and lineage.connected(
                    source_nodes[key.lower()],
                    (scope_id, candidate_alias, column),
                )
            )
            if not candidates:
                if anchored_dependency:
                    continue
                _unavailable(
                    OptimizationCode.UNBOUNDED_RELATION,
                    f"Atlas cannot derive {key!r} for relation "
                    f"{table.sql(dialect='duckdb')}.",
                    sql_fragment=table.sql(dialect="duckdb"),
                    documentation_anchor="join-lineage",
                )
            bindings.append(
                RelationKeyBinding(
                    source_column=key,
                    relation_column=candidates[0],
                )
            )
        if anchored_dependency and not bindings:
            continue
        scans.append(
            CatalogueScan(
                ordinal=ordinal,
                relation=table.name,
                alias=table.alias_or_name or table.name,
                key_bindings=tuple(bindings),
            )
        )
    root_scope = scopes[-1]
    missing = tuple(
        key
        for key in keys
        if not (
            lineage.connected(
                source_nodes[key.lower()],
                (id(root_scope), "$output", key.lower()),
            )
            or _root_full_join_coalesced_key_is_safe(
                root_scope,
                key=key,
                lineage=lineage,
                source_node=source_nodes[key.lower()],
                output_names=output_names,
            )
        )
    )
    if missing:
        _unavailable(
            OptimizationCode.KEY_NOT_PRESERVED,
            "The query does not project every stable key unchanged; missing "
            + ", ".join(missing)
            + ".",
            sql_fragment=", ".join(missing),
            documentation_anchor="stable-output-key",
        )
    return CatalogueQueryPlan(
        compiler_version=_COMPILER_VERSION,
        normalized_sql=query.sql(dialect="duckdb", pretty=True),
        scans=tuple(scans),
    )


def _is_materialized_driver_dependency(
    *,
    scope: Scope,
    alias: str,
    source_scope: Scope,
    source_alias: str,
    lineage: ColumnLineage,
) -> bool:
    """Recognize equijoin-dependent scans below an explicit driver barrier."""

    source_parent = source_scope.expression.parent
    if (
        scope is source_scope
        or not source_scope.is_cte
        or not isinstance(source_parent, exp.CTE)
        or source_parent.args.get("materialized") is not True
    ):
        return False
    source_columns = tuple(
        node
        for node in lineage.nodes
        if node[0] == id(source_scope) and node[1] == source_alias
    )
    relation_columns = tuple(
        node
        for node in lineage.nodes
        if node[0] == id(scope) and node[1] == alias
    )
    return any(
        lineage.connected(source_column, relation_column)
        for source_column in source_columns
        for relation_column in relation_columns
    )


def _has_preserved_driver_path(
    *,
    root_scope: Scope,
    source_scope: Scope,
    source_alias: str,
    analysis: AnalyzedCatalogueQuery,
) -> bool:
    pending = [root_scope]
    visited: set[int] = set()
    while pending:
        scope = pending.pop()
        if id(scope) in visited:
            continue
        visited.add(id(scope))
        nullable = (
            analysis.facts_for(scope).nullable_relations
            - analysis.facts_for(scope).full_join_nullable_relations
        )
        if scope is source_scope and source_alias not in nullable:
            return True
        pending.extend(
            selected
            for alias, (_, selected) in scope.selected_sources.items()
            if isinstance(selected, Scope) and alias not in nullable
        )
    return False


def _scope_output_names(
    scope: Scope,
    *,
    keys: tuple[str, ...],
) -> frozenset[str]:
    if isinstance(scope.expression, exp.SetOperation):
        names = _set_output_names(scope.expression)
        if names is None or len(set(names)) != len(names):
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "Nested set operations require unique explicit output names.",
                sql_fragment=scope.expression.sql(dialect="duckdb"),
                documentation_anchor="set-operations",
            )
        return frozenset(names)
    if not isinstance(scope.expression, exp.Select):
        lateral_select = _row_local_lateral_select(scope.expression)
        if lateral_select is not None:
            names = tuple(
                selection.alias_or_name.lower()
                for selection in lateral_select.expressions
                if selection.alias_or_name
            )
            if (
                len(names) != len(lateral_select.expressions)
                or len(set(names)) != len(names)
            ):
                _unavailable(
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                    "Row-local lateral projections require unique output "
                    "names.",
                    sql_fragment=scope.expression.sql(dialect="duckdb"),
                    documentation_anchor="row-local-expansion",
                )
            return frozenset(names)
        names = tuple(column.lower() for column in scope.outer_columns)
        if not names or len(set(names)) != len(names):
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "Row-expansion relations require unique output column aliases.",
                sql_fragment=scope.expression.sql(dialect="duckdb"),
                documentation_anchor="row-local-expansion",
            )
        return frozenset(names)
    wildcard_selections = tuple(
        selection
        for selection in scope.expression.expressions
        if _star_expression(selection) is not None
    )
    if scope.outer_columns:
        if wildcard_selections:
            _unavailable(
                OptimizationCode.UNSUPPORTED_PROJECTION,
                "Wildcard expansion cannot be mapped to an explicit CTE "
                "column list without source schema metadata.",
                sql_fragment=scope.expression.sql(dialect="duckdb"),
                documentation_anchor="wildcard-projections",
            )
        names = tuple(column.lower() for column in scope.outer_columns)
        if (
            len(names) != len(scope.expression.expressions)
            or len(set(names)) != len(names)
        ):
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "Explicit CTE column lists must name every output exactly once.",
                sql_fragment=scope.expression.sql(dialect="duckdb"),
                documentation_anchor="scope-lineage",
            )
        return frozenset(names)
    names = {
        selection.alias_or_name.lower()
        for selection in scope.expression.expressions
        if selection.alias_or_name
    }
    for selection in wildcard_selections:
        _wildcard_source_alias(scope, selection, keys=keys)
        names.update(key.lower() for key in keys)
    return frozenset(names)


def _validate_scope_shape(scope: Scope) -> None:
    query = scope.expression
    assert isinstance(query, exp.Select)
    unsupported = next(
        (
            name
            for name in _UNSUPPORTED_SELECT_ARGS
            if query.args.get(name) is not None
            and not (
                name in {"limit", "offset"}
                and _is_scalar_subquery_scope(scope)
            )
        ),
        None,
    )
    if unsupported is not None:
        node = query.args[unsupported]
        label = _SELECT_ARG_LABELS.get(
            unsupported, unsupported.replace("_", " ")
        )
        _unavailable(
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            f"Queries containing {label} are not optimized yet.",
            sql_fragment=_sql_fragment(node),
            documentation_anchor="supported-subset",
        )
def _add_explicit_output_lineage(
    scope: Scope,
    *,
    lineage: ColumnLineage,
) -> None:
    if not scope.outer_columns:
        return
    for selection, outer_name in zip(
        scope.expression.expressions,
        scope.outer_columns,
        strict=True,
    ):
        inner_name = selection.alias_or_name
        if not inner_name:
            continue
        lineage.union(
            (id(scope), "$output", inner_name.lower()),
            (id(scope), "$output", outer_name.lower()),
        )


def _validate_scope_projections(
    scope: Scope,
    *,
    keys: tuple[str, ...],
    lineage: ColumnLineage,
    output_names: dict[int, frozenset[str]],
    using_columns: set[str],
) -> None:
    names: list[str] = []
    for index, selection in enumerate(scope.expression.expressions):
        if (
            (
                len(scope.expression.expressions) == 1
                and _is_scalar_subquery_scope(scope)
            )
            or isinstance(scope.expression.parent, exp.Exists)
        ):
            continue
        projected = (
            selection.this if isinstance(selection, exp.Alias) else selection
        )
        if _star_expression(projected) is not None:
            source_alias = _wildcard_source_alias(
                scope,
                projected,
                keys=keys,
            )
            for key in keys:
                normalized_key = key.lower()
                names.append(normalized_key)
                input_node = _boundary_node(
                    scope,
                    source_alias,
                    key,
                    lineage=lineage,
                    output_names=output_names,
                )
                lineage.union(
                    (id(scope), "$output", normalized_key),
                    input_node,
                )
            continue
        explicit_output_name = (
            scope.outer_columns[index]
            if index < len(scope.outer_columns)
            else None
        )
        output_name = explicit_output_name or selection.alias_or_name
        if not output_name:
            _unavailable(
                OptimizationCode.UNSUPPORTED_PROJECTION,
                "Every scoped expression needs a stable output name.",
                sql_fragment=selection.sql(dialect="duckdb"),
                documentation_anchor="scope-lineage",
            )
        names.append(output_name.lower())
        expression = (
            selection.this if isinstance(selection, exp.Alias) else selection
        )
        if not isinstance(expression, exp.Column) and not isinstance(
            selection, exp.Alias
        ) and explicit_output_name is None:
            _unavailable(
                OptimizationCode.UNSUPPORTED_PROJECTION,
                "Computed result expressions require an explicit alias.",
                sql_fragment=selection.sql(dialect="duckdb"),
                documentation_anchor="supported-subset",
            )
        if not isinstance(expression, exp.Column):
            continue
        output_node = (id(scope), "$output", output_name.lower())
        if expression.table:
            if (
                expression.table.lower() not in scope.sources
                and scope.is_correlated_subquery
            ):
                lineage.add(output_node)
                continue
            input_node = _boundary_node(
                scope,
                expression.table,
                expression.name,
                lineage=lineage,
                output_names=output_names,
            )
            lineage.union(output_node, input_node)
            continue
        selected_aliases = tuple(scope.selected_sources)
        if len(selected_aliases) == 1:
            input_node = _boundary_node(
                scope,
                selected_aliases[0],
                expression.name,
                lineage=lineage,
                output_names=output_names,
            )
            lineage.union(output_node, input_node)
        elif expression.name.lower() in using_columns:
            for alias in selected_aliases:
                node = _boundary_node(
                    scope,
                    alias,
                    expression.name,
                    lineage=lineage,
                    output_names=output_names,
                    require_child_output=True,
                )
                if node is not None:
                    lineage.union(output_node, node)
    if len(set(names)) != len(names):
        _unavailable(
            OptimizationCode.UNSUPPORTED_PROJECTION,
            "Result column names must be unique within every scope.",
            documentation_anchor="scope-lineage",
        )


def _is_scalar_subquery_scope(scope: Scope) -> bool:
    subquery = scope.expression.parent
    return (
        isinstance(subquery, exp.Subquery)
        and not isinstance(subquery.parent, (exp.From, exp.Join))
    )


def _inside_correlated_scalar_subquery(scope: Scope) -> bool:
    owner: Scope | None = scope
    while owner is not None:
        if owner.is_correlated_subquery and _is_scalar_subquery_scope(owner):
            return True
        owner = owner.parent
    return False


def _star_expression(
    expression: exp.Expression,
) -> exp.Star | None:
    projected = (
        expression.this if isinstance(expression, exp.Alias) else expression
    )
    if isinstance(projected, exp.Star):
        return projected
    if (
        isinstance(projected, exp.Column)
        and isinstance(projected.this, exp.Star)
    ):
        return projected.this
    return None


def _wildcard_source_alias(
    scope: Scope,
    expression: exp.Expression,
    *,
    keys: tuple[str, ...],
) -> str:
    star = _star_expression(expression)
    if star is None:
        raise AssertionError("wildcard source requested for a non-wildcard")
    _validate_wildcard_key_modifiers(
        star,
        keys=keys,
        sql_fragment=expression.sql(dialect="duckdb"),
    )
    projected = (
        expression.this if isinstance(expression, exp.Alias) else expression
    )
    if isinstance(projected, exp.Column) and projected.table:
        alias = projected.table.lower()
        if alias not in scope.selected_sources:
            _unavailable(
                OptimizationCode.UNSUPPORTED_PROJECTION,
                f"Wildcard source {projected.table!r} cannot be resolved.",
                sql_fragment=expression.sql(dialect="duckdb"),
                documentation_anchor="wildcard-projections",
            )
        return alias
    selected_aliases = tuple(scope.selected_sources)
    if len(selected_aliases) != 1:
        _unavailable(
            OptimizationCode.UNSUPPORTED_PROJECTION,
            "An unqualified wildcard is ambiguous in a multi-relation scope.",
            sql_fragment=expression.sql(dialect="duckdb"),
            documentation_anchor="wildcard-projections",
        )
    return selected_aliases[0]


def _validate_wildcard_key_modifiers(
    star: exp.Star,
    *,
    keys: tuple[str, ...],
    sql_fragment: str,
) -> None:
    normalized_keys = {key.lower() for key in keys}
    if star.args.get("ilike") is not None:
        _unavailable(
            OptimizationCode.UNSUPPORTED_PROJECTION,
            "Wildcard pattern selection requires source schema metadata.",
            sql_fragment=sql_fragment,
            documentation_anchor="wildcard-projections",
        )
    excluded = star.args.get("except_") or ()
    for column in excluded:
        if not isinstance(column, exp.Column):
            _unavailable(
                OptimizationCode.UNSUPPORTED_PROJECTION,
                "Wildcard exclusions must name explicit columns.",
                sql_fragment=sql_fragment,
                documentation_anchor="wildcard-projections",
            )
        if column.name.lower() in normalized_keys:
            _unavailable(
                OptimizationCode.UNSUPPORTED_PROJECTION,
                "Wildcard EXCLUDE cannot remove a stable key column.",
                sql_fragment=sql_fragment,
                documentation_anchor="wildcard-projections",
            )
    replacements = star.args.get("replace") or ()
    for replacement in replacements:
        if (
            not isinstance(replacement, exp.Alias)
            or not replacement.alias
        ):
            _unavailable(
                OptimizationCode.UNSUPPORTED_PROJECTION,
                "Wildcard replacements require explicit output names.",
                sql_fragment=sql_fragment,
                documentation_anchor="wildcard-projections",
            )
        if replacement.alias.lower() in normalized_keys:
            _unavailable(
                OptimizationCode.UNSUPPORTED_PROJECTION,
                "Wildcard REPLACE cannot transform a stable key column.",
                sql_fragment=sql_fragment,
                documentation_anchor="wildcard-projections",
            )
    renames = star.args.get("rename") or ()
    for rename in renames:
        if (
            not isinstance(rename, exp.Alias)
            or not isinstance(rename.this, exp.Column)
            or not rename.alias
        ):
            _unavailable(
                OptimizationCode.UNSUPPORTED_PROJECTION,
                "Wildcard renames must map explicit column names.",
                sql_fragment=sql_fragment,
                documentation_anchor="wildcard-projections",
            )
        if (
            rename.this.name.lower() in normalized_keys
            or rename.alias.lower() in normalized_keys
        ):
            _unavailable(
                OptimizationCode.UNSUPPORTED_PROJECTION,
                "Wildcard RENAME cannot rename a stable key column or "
                "introduce a conflicting stable-key name.",
                sql_fragment=sql_fragment,
                documentation_anchor="wildcard-projections",
            )


def _add_scope_join_lineage(
    scope: Scope,
    *,
    keys: tuple[str, ...],
    lineage: ColumnLineage,
    output_names: dict[int, frozenset[str]],
    using_columns: set[str],
    relational_facts: ScopeRelationalFacts,
) -> None:
    from_clause = scope.expression.args.get("from_")
    available_aliases: list[str] = []
    if from_clause is not None and from_clause.this is not None:
        available_aliases.append(from_clause.this.alias_or_name.lower())
    for join in scope.expression.args.get("joins") or ():
        side = str(join.args.get("side") or "").upper()
        kind = str(join.args.get("kind") or "").upper()
        row_expansion = _row_local_unnest(join.this, available_aliases)
        lateral_projection = (
            _row_local_lateral_select(
                join.this,
                available_aliases=available_aliases,
            )
            is not None
        )
        if (
            isinstance(join.this, exp.Lateral)
            and not row_expansion
            and not lateral_projection
        ):
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "LATERAL queries must be row-local projections without "
                "independent relations or cardinality-changing clauses.",
                sql_fragment=join.sql(dialect="duckdb"),
                documentation_anchor="row-local-expansion",
            )
        inner = not side and kind in {"", "INNER"}
        left_join = side == "LEFT" and kind in {"", "OUTER"}
        right_join = side == "RIGHT" and kind in {"", "OUTER"}
        full_join = side == "FULL" and kind in {"", "OUTER"}
        if full_join:
            using = tuple(join.args.get("using") or ())
            joined_columns = {
                identifier.name.lower()
                for identifier in using
                if isinstance(identifier, exp.Identifier)
            }
            missing_keys = tuple(
                key for key in keys if key.lower() not in joined_columns
            )
            if using and missing_keys:
                _unavailable(
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                    "FULL JOIN must use every stable key column; missing "
                    + ", ".join(missing_keys)
                    + ".",
                    sql_fragment=join.sql(dialect="duckdb"),
                    documentation_anchor="join-lineage",
                )
            if not using and join.args.get("on") is None:
                _unavailable(
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                    "FULL JOIN requires complete stable-key USING columns "
                    "or a proven equijoin with a coalesced result key.",
                    sql_fragment=join.sql(dialect="duckdb"),
                    documentation_anchor="join-lineage",
                )
        if (
            not inner
            and not left_join
            and not right_join
            and not full_join
            and not row_expansion
            and not lateral_projection
        ):
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "Only inner and key-bounded left/right joins are optimized "
                "currently.",
                sql_fragment=join.sql(dialect="duckdb"),
                documentation_anchor="join-lineage",
            )
        right_alias = join.this.alias_or_name.lower()
        if inner:
            for component in relational_facts.equivalence_classes:
                nodes = tuple(
                    _boundary_node(
                        scope,
                        column.relation,
                        column.column,
                        lineage=lineage,
                        output_names=output_names,
                        require_child_output=True,
                    )
                    for column in component
                )
                present = tuple(node for node in nodes if node is not None)
                for node in present[1:]:
                    lineage.union(present[0], node)
        for identifier in join.args.get("using") or ():
            if not isinstance(identifier, exp.Identifier):
                _unavailable(
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                    "JOIN USING columns must be simple identifiers.",
                    sql_fragment=join.sql(dialect="duckdb"),
                    documentation_anchor="join-lineage",
                )
            column = identifier.name.lower()
            using_columns.add(column)
            if inner:
                continue
            right = _boundary_node(
                scope,
                right_alias,
                column,
                lineage=lineage,
                output_names=output_names,
            )
            for alias in available_aliases:
                left_node = _boundary_node(
                    scope,
                    alias,
                    column,
                    lineage=lineage,
                    output_names=output_names,
                    require_child_output=True,
                )
                if left_node is not None:
                    lineage.union(left_node, right)
        condition = join.args.get("on")
        if condition is None:
            available_aliases.append(right_alias)
            continue
        if inner:
            available_aliases.append(right_alias)
            continue
        for predicate in _conjuncts(condition):
            if not isinstance(predicate, (exp.EQ, exp.NullSafeEQ)):
                continue
            left = predicate.this
            right = predicate.expression
            if (
                isinstance(left, exp.Column)
                and isinstance(right, exp.Column)
                and left.table
                and right.table
            ):
                lineage.union(
                    _boundary_node(
                        scope,
                        left.table,
                        left.name,
                        lineage=lineage,
                        output_names=output_names,
                    ),
                    _boundary_node(
                        scope,
                        right.table,
                        right.name,
                        lineage=lineage,
                        output_names=output_names,
                    ),
                )
        available_aliases.append(right_alias)


def _validate_full_join_key_projection(
    scope: Scope,
    *,
    keys: tuple[str, ...],
    is_root: bool,
) -> None:
    full_joins = tuple(
        join
        for join in scope.expression.args.get("joins") or ()
        if str(join.args.get("side") or "").upper() == "FULL"
    )
    if not full_joins:
        return
    on_joins = tuple(
        join for join in full_joins if not join.args.get("using")
    )
    if on_joins and (not is_root or len(full_joins) != 1):
        _unavailable(
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            "FULL JOIN ON with a coalesced key is supported only as one "
            "direct root join.",
            sql_fragment=scope.expression.sql(dialect="duckdb"),
            documentation_anchor="join-lineage",
        )
    for key in keys:
        merged = tuple(
            selection
            for selection in scope.expression.expressions
            if _is_merged_using_key_selection(selection, key=key)
        )
        coalesced = tuple(
            selection
            for selection in scope.expression.expressions
            if _coalesced_key_columns(selection, key=key) is not None
        )
        expected = coalesced if on_joins else merged
        if len(expected) != 1:
            form = "coalesced" if on_joins else "merged"
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                f"FULL JOIN scopes must project each {form} stable key "
                f"exactly once; missing {key}.",
                sql_fragment=scope.expression.sql(dialect="duckdb"),
                documentation_anchor="join-lineage",
            )
        if on_joins:
            columns = _coalesced_key_columns(expected[0], key=key)
            assert columns is not None
            aliases = {column.table.lower() for column in columns}
            selected_aliases = set(scope.selected_sources)
            if len(columns) != 2 or aliases != selected_aliases:
                _unavailable(
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                    "A root FULL JOIN ON key must coalesce exactly one "
                    "qualified column from each preserved side.",
                    sql_fragment=expected[0].sql(dialect="duckdb"),
                    documentation_anchor="join-lineage",
                )


def _is_merged_using_key_selection(
    selection: exp.Expression,
    *,
    key: str,
) -> bool:
    expression = (
        selection.this if isinstance(selection, exp.Alias) else selection
    )
    return (
        selection.alias_or_name.lower() == key.lower()
        and isinstance(expression, exp.Column)
        and not expression.table
        and expression.name.lower() == key.lower()
    )


def _coalesced_key_columns(
    selection: exp.Expression,
    *,
    key: str,
) -> tuple[exp.Column, ...] | None:
    if (
        not isinstance(selection, exp.Alias)
        or selection.alias.lower() != key.lower()
        or not isinstance(selection.this, exp.Coalesce)
    ):
        return None
    arguments = (selection.this.this, *selection.this.expressions)
    if not all(
        isinstance(argument, exp.Column) and argument.table
        for argument in arguments
    ):
        return None
    return tuple(arguments)


def _root_full_join_coalesced_key_is_safe(
    root_scope: Scope,
    *,
    key: str,
    lineage: ColumnLineage,
    source_node: ColumnNode,
    output_names: dict[int, frozenset[str]],
) -> bool:
    full_on_joins = tuple(
        join
        for join in root_scope.expression.args.get("joins") or ()
        if (
            str(join.args.get("side") or "").upper() == "FULL"
            and not join.args.get("using")
        )
    )
    if len(full_on_joins) != 1:
        return False
    selections = tuple(
        selection
        for selection in root_scope.expression.expressions
        if _coalesced_key_columns(selection, key=key) is not None
    )
    if len(selections) != 1:
        return False
    columns = _coalesced_key_columns(selections[0], key=key)
    assert columns is not None
    return all(
        lineage.connected(
            source_node,
            _boundary_node(
                root_scope,
                column.table,
                column.name,
                lineage=lineage,
                output_names=output_names,
            ),
        )
        for column in columns
    )


def _validate_expansion_scope(scope: Scope) -> None:
    expression = scope.expression
    if isinstance(expression, exp.Unnest):
        return
    if isinstance(expression, exp.Lateral) and isinstance(
        expression.this, exp.Unnest
    ):
        return
    if _row_local_lateral_select(expression) is not None:
        return
    _unavailable(
        OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
        "Only row-local UNNEST and lateral projection scopes are optimized.",
        sql_fragment=expression.sql(dialect="duckdb"),
        documentation_anchor="row-local-expansion",
    )


def _row_local_lateral_select(
    expression: exp.Expression,
    *,
    available_aliases: list[str] | None = None,
) -> exp.Select | None:
    if not isinstance(expression, exp.Lateral):
        return None
    candidate = expression.this
    if not isinstance(candidate, exp.Subquery) or not isinstance(
        candidate.this,
        exp.Select,
    ):
        return None
    select = candidate.this
    if any(
        select.args.get(argument)
        for argument in (
            "distinct",
            "from_",
            "group",
            "having",
            "joins",
            "laterals",
            "limit",
            "offset",
            "order",
            "qualify",
            "windows",
            "with_",
        )
    ):
        return None
    if any(
        isinstance(node, (exp.AggFunc, exp.Explode, exp.Unnest, exp.Window))
        for node in select.walk()
    ):
        return None
    if any(query is not select for query in select.find_all(exp.Query)):
        return None
    if not select.expressions or any(
        not selection.alias_or_name
        or _star_expression(selection) is not None
        for selection in select.expressions
    ):
        return None
    if available_aliases is None:
        return select
    available = {alias.lower() for alias in available_aliases}
    for column in select.find_all(exp.Column):
        if column.table:
            if column.table.lower() not in available:
                return None
        elif len(available) != 1:
            return None
    return select


def _row_local_unnest(
    expression: exp.Expression,
    available_aliases: list[str],
) -> bool:
    candidate = expression
    if isinstance(candidate, exp.Lateral):
        candidate = candidate.this
    if not isinstance(candidate, exp.Unnest):
        return False
    if next(candidate.find_all(exp.Query), None) is not None:
        return False
    available = {alias.lower() for alias in available_aliases}
    for column in candidate.find_all(exp.Column):
        if column.table:
            if column.table.lower() not in available:
                return False
        elif len(available) != 1:
            return False
    return True


def _physical_tables(source: Scope | exp.Expression) -> tuple[exp.Table, ...]:
    if isinstance(source, exp.Table):
        return (source,)
    if not isinstance(source, Scope):
        return ()
    return tuple(
        selected
        for _, selected in source.selected_sources.values()
        if isinstance(selected, exp.Table)
    ) + tuple(
        table
        for _, selected in source.selected_sources.values()
        if isinstance(selected, Scope)
        for table in _physical_tables(selected)
    )


def _boundary_node(
    scope: Scope,
    alias: str,
    column: str,
    *,
    lineage: ColumnLineage,
    output_names: dict[int, frozenset[str]],
    require_child_output: bool = False,
) -> ColumnNode | None:
    normalized_alias = alias.lower()
    normalized_column = column.lower()
    source = scope.sources.get(normalized_alias)
    if source is None:
        if require_child_output:
            return None
        _unavailable(
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            f"Column source {alias!r} cannot be resolved in its scope.",
            sql_fragment=f"{alias}.{column}",
            documentation_anchor="scope-lineage",
        )
    node = (id(scope), normalized_alias, normalized_column)
    lineage.add(node)
    if isinstance(source, Scope):
        if normalized_column not in output_names[id(source)]:
            if require_child_output:
                return None
            return node
        lineage.union(
            node,
            (id(source), "$output", normalized_column),
        )
    return node


def _validate_physical_table(table: exp.Table) -> None:
    if (
        isinstance(table.this, exp.Anonymous)
        and table.db.lower() == "macros"
    ):
        _unavailable(
            OptimizationCode.UNSUPPORTED_FUNCTION,
            f"Atlas has no stored definition for "
            f"{table.sql(dialect='duckdb')}.",
            sql_fragment=table.sql(dialect="duckdb"),
            documentation_anchor="macro-expansion",
        )
    if not isinstance(table.this, exp.Identifier):
        _unavailable(
            OptimizationCode.UNSUPPORTED_RELATION,
            "Table functions and dynamic relations are not optimized.",
            sql_fragment=table.sql(dialect="duckdb"),
            documentation_anchor="supported-subset",
        )
    if table.name.lower() in _RESERVED_RELATIONS:
        _unavailable(
            OptimizationCode.RESERVED_RELATION,
            f"Relation {table.name!r} is reserved for catalogue execution.",
            sql_fragment=table.sql(dialect="duckdb"),
            documentation_anchor="query-boundary",
        )


def _validate_managed_relations(query: exp.Query) -> None:
    for table in query.find_all(exp.Table):
        function_name = _relation_function_name(table)
        if function_name is None:
            continue
        if function_name in _DYNAMIC_RELATION_FUNCTIONS:
            _unavailable(
                OptimizationCode.UNMANAGED_RELATION,
                f"Dynamic relation {function_name.upper()} is outside the "
                "managed catalogue boundary.",
                sql_fragment=f"{function_name.upper()}(...)",
                documentation_anchor="managed-relations",
            )
        if (
            function_name.startswith("read_")
            or function_name in _EXTERNAL_RELATION_FUNCTIONS
        ):
            _unavailable(
                OptimizationCode.UNMANAGED_RELATION,
                f"External scan {function_name.upper()} is outside the "
                "managed catalogue boundary.",
                sql_fragment=f"{function_name.upper()}(...)",
                documentation_anchor="managed-relations",
            )


def _relation_function_name(table: exp.Table) -> str | None:
    relation = table.this
    if isinstance(relation, exp.Anonymous):
        return relation.name.lower()
    if isinstance(relation, exp.Func):
        return relation.sql_name().lower()
    return None


def _validate_keyed_grouping(
    scopes: tuple[Scope, ...],
    *,
    lineage: ColumnLineage,
    source_nodes: dict[str, ColumnNode],
    keys: tuple[str, ...],
    output_names: dict[int, frozenset[str]],
) -> None:
    for scope in scopes:
        aggregates = tuple(_scope_aggregates(scope))
        group = scope.expression.args.get("group")
        if not aggregates and group is None:
            continue
        if _inside_correlated_scalar_subquery(scope):
            continue
        if not aggregates or group is None:
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "Aggregated scopes must group exactly by the stable key.",
                sql_fragment=scope.expression.sql(dialect="duckdb"),
                documentation_anchor="keyed-aggregation",
            )
        expressions = (
            _group_by_all_expressions(scope)
            if group.args.get("all")
            else tuple(group.expressions)
        )
        if len(expressions) != len(keys):
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "Aggregated scopes must group exactly by the stable key.",
                sql_fragment=group.sql(dialect="duckdb"),
                documentation_anchor="keyed-aggregation",
            )
        group_nodes: list[ColumnNode] = []
        for expression in expressions:
            if not isinstance(expression, exp.Column):
                _unavailable(
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                    "Stable grouping expressions must be unchanged columns.",
                    sql_fragment=expression.sql(dialect="duckdb"),
                    documentation_anchor="keyed-aggregation",
                )
            if expression.table:
                node = _boundary_node(
                    scope,
                    expression.table,
                    expression.name,
                    lineage=lineage,
                    output_names=output_names,
                )
            elif len(scope.selected_sources) == 1:
                node = _boundary_node(
                    scope,
                    next(iter(scope.selected_sources)),
                    expression.name,
                    lineage=lineage,
                    output_names=output_names,
                )
            else:
                _unavailable(
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                    "Grouping columns must be qualified in multi-relation scopes.",
                    sql_fragment=expression.sql(dialect="duckdb"),
                    documentation_anchor="keyed-aggregation",
                )
            group_nodes.append(node)
        if any(
            sum(
                lineage.connected(source_nodes[key.lower()], node)
                for node in group_nodes
            )
            != 1
            for key in keys
        ):
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "Every stable key must identify exactly one grouping column.",
                sql_fragment=group.sql(dialect="duckdb"),
                documentation_anchor="keyed-aggregation",
            )


def _validate_arg_extreme_aggregates(
    scopes: tuple[Scope, ...],
    *,
    lineage: ColumnLineage,
    source_nodes: dict[str, ColumnNode],
    keys: tuple[str, ...],
    output_names: dict[int, frozenset[str]],
) -> None:
    for scope in scopes:
        for function in _scope_aggregates(scope):
            if not isinstance(function, _ARG_EXTREME_AGGREGATE_TYPES):
                continue
            count = function.args.get("count")
            if count is not None and not (
                isinstance(count, exp.Literal)
                and count.is_int
                and int(count.this) > 0
            ):
                _unavailable(
                    OptimizationCode.UNSUPPORTED_FUNCTION,
                    "ARG_MIN and ARG_MAX top-N counts must be positive "
                    "integer literals.",
                    sql_fragment=function.sql(dialect="duckdb"),
                    documentation_anchor="keyed-aggregation",
                )
            ordering = function.expression
            ordered_expressions = (
                tuple(ordering.expressions)
                if isinstance(ordering, exp.Tuple)
                else (ordering,)
            )
            ordered_nodes = _direct_order_nodes(
                scope,
                ordered_expressions,
                lineage=lineage,
                output_names=output_names,
            )
            if _expression_is_order_determined(
                scope,
                function.this,
                ordered_expressions=ordered_expressions,
                ordered_nodes=ordered_nodes,
                lineage=lineage,
                source_nodes=source_nodes,
                keys=keys,
                output_names=output_names,
            ):
                continue
            _unavailable(
                OptimizationCode.UNSUPPORTED_FUNCTION,
                "ARG_MIN and ARG_MAX ordering must determine the emitted "
                "value.",
                sql_fragment=function.sql(dialect="duckdb"),
                documentation_anchor="keyed-aggregation",
            )


def _group_by_all_expressions(scope: Scope) -> tuple[exp.Expression, ...]:
    if not isinstance(scope.expression, exp.Select):
        return ()
    expressions: list[exp.Expression] = []
    for selection in scope.expression.expressions:
        if next(selection.find_all(exp.AggFunc), None) is not None:
            continue
        projected = (
            selection.this if isinstance(selection, exp.Alias) else selection
        )
        if next(projected.find_all(exp.Column), None) is None:
            continue
        expressions.append(projected)
    return tuple(expressions)


def _validate_key_local_windows(
    scopes: tuple[Scope, ...],
    *,
    lineage: ColumnLineage,
    source_nodes: dict[str, ColumnNode],
    keys: tuple[str, ...],
    output_names: dict[int, frozenset[str]],
) -> None:
    for scope in scopes:
        windows = tuple(_scope_windows(scope))
        row_choice_windows = tuple(
            window
            for window in windows
            if isinstance(window.this, _ROW_CHOICE_WINDOW_FUNCTION_TYPES)
        )
        if row_choice_windows:
            reference = row_choice_windows[0]
            if len(row_choice_windows) != len(windows) or any(
                window.args.get("partition_by")
                != reference.args.get("partition_by")
                or window.args.get("order") != reference.args.get("order")
                for window in row_choice_windows[1:]
            ):
                _unavailable(
                    OptimizationCode.UNSUPPORTED_FUNCTION,
                    "Row-choice windows in one scope must share one "
                    "partition and ordering.",
                    sql_fragment=scope.expression.sql(dialect="duckdb"),
                    documentation_anchor="key-local-windows",
                )
        for window in windows:
            function = window.this
            if not isinstance(
                function,
                (
                    *_KEY_LOCAL_WINDOW_FUNCTION_TYPES,
                    *_ROW_CHOICE_WINDOW_FUNCTION_TYPES,
                ),
            ):
                _unavailable(
                    OptimizationCode.UNSUPPORTED_FUNCTION,
                    f"Atlas cannot prove that window function "
                    f"{function.sql(dialect='duckdb')} is deterministic.",
                    sql_fragment=window.sql(dialect="duckdb"),
                    documentation_anchor="key-local-windows",
                )
            partition_nodes: list[ColumnNode] = []
            for expression in window.args.get("partition_by") or ():
                if not isinstance(expression, exp.Column):
                    continue
                if expression.table:
                    node = _boundary_node(
                        scope,
                        expression.table,
                        expression.name,
                        lineage=lineage,
                        output_names=output_names,
                    )
                elif len(scope.selected_sources) == 1:
                    node = _boundary_node(
                        scope,
                        next(iter(scope.selected_sources)),
                        expression.name,
                        lineage=lineage,
                        output_names=output_names,
                    )
                else:
                    continue
                partition_nodes.append(node)
            missing = tuple(
                key
                for key in keys
                if not any(
                    lineage.connected(source_nodes[key.lower()], node)
                    for node in partition_nodes
                )
            )
            if missing:
                _unavailable(
                    OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                    "Every window partition must include the stable key; "
                    "missing " + ", ".join(missing) + ".",
                    sql_fragment=window.sql(dialect="duckdb"),
                    documentation_anchor="key-local-windows",
                )
            if isinstance(function, _ROW_CHOICE_WINDOW_FUNCTION_TYPES):
                _validate_row_choice_window(
                    scope,
                    window,
                    lineage=lineage,
                    source_nodes=source_nodes,
                    keys=keys,
                    output_names=output_names,
                )


def _validate_row_choice_window(
    scope: Scope,
    window: exp.Window,
    *,
    lineage: ColumnLineage,
    source_nodes: dict[str, ColumnNode],
    keys: tuple[str, ...],
    output_names: dict[int, frozenset[str]],
) -> None:
    order = window.args.get("order")
    if not isinstance(order, exp.Order) or not order.expressions:
        _unavailable(
            OptimizationCode.UNSUPPORTED_FUNCTION,
            "Row-choice windows require deterministic ordering.",
            sql_fragment=window.sql(dialect="duckdb"),
            documentation_anchor="key-local-windows",
        )
    ordered_expressions = tuple(
        ordered.this for ordered in order.expressions
    )
    ordered_nodes = _direct_order_nodes(
        scope,
        ordered_expressions,
        lineage=lineage,
        output_names=output_names,
    )
    for selection in scope.expression.expressions:
        projected = (
            selection.this if isinstance(selection, exp.Alias) else selection
        )
        nested_windows = tuple(projected.find_all(exp.Window))
        if nested_windows:
            if projected not in nested_windows:
                _unavailable(
                    OptimizationCode.UNSUPPORTED_FUNCTION,
                    "Row-choice window outputs must be direct projections.",
                    sql_fragment=selection.sql(dialect="duckdb"),
                    documentation_anchor="key-local-windows",
                )
            continue
        if _star_expression(projected) is not None or not (
            _expression_is_order_determined(
                scope,
                projected,
                ordered_expressions=ordered_expressions,
                ordered_nodes=ordered_nodes,
                lineage=lineage,
                source_nodes=source_nodes,
                keys=keys,
                output_names=output_names,
            )
        ):
            _unavailable(
                OptimizationCode.UNSUPPORTED_FUNCTION,
                "Window ordering must determine every projected value.",
                sql_fragment=selection.sql(dialect="duckdb"),
                documentation_anchor="key-local-windows",
            )
    function = window.this
    if isinstance(function, exp.RowNumber):
        return
    if isinstance(function, exp.Ntile):
        buckets = function.this
        if not (
            isinstance(buckets, exp.Literal)
            and buckets.is_int
            and int(buckets.this) > 0
        ):
            _unavailable(
                OptimizationCode.UNSUPPORTED_FUNCTION,
                "NTILE requires a positive integer literal bucket count.",
                sql_fragment=window.sql(dialect="duckdb"),
                documentation_anchor="key-local-windows",
            )
        return
    value = function.this
    if not _expression_is_order_determined(
        scope,
        value,
        ordered_expressions=ordered_expressions,
        ordered_nodes=ordered_nodes,
        lineage=lineage,
        source_nodes=source_nodes,
        keys=keys,
        output_names=output_names,
    ):
        _unavailable(
            OptimizationCode.UNSUPPORTED_FUNCTION,
            "Window ordering must determine the selected value.",
            sql_fragment=window.sql(dialect="duckdb"),
            documentation_anchor="key-local-windows",
        )
    offset = function.args.get("offset")
    if offset is not None and not (
        isinstance(offset, exp.Literal)
        and offset.is_int
        and int(offset.this) >= 0
    ):
        _unavailable(
            OptimizationCode.UNSUPPORTED_FUNCTION,
            "Window offsets must be non-negative integer literals.",
            sql_fragment=window.sql(dialect="duckdb"),
            documentation_anchor="key-local-windows",
        )
    default = function.args.get("default")
    if default is not None and not _expression_is_order_determined(
        scope,
        default,
        ordered_expressions=ordered_expressions,
        ordered_nodes=ordered_nodes,
        lineage=lineage,
        source_nodes=source_nodes,
        keys=keys,
        output_names=output_names,
    ):
        _unavailable(
            OptimizationCode.UNSUPPORTED_FUNCTION,
            "Window ordering must determine the offset default.",
            sql_fragment=window.sql(dialect="duckdb"),
            documentation_anchor="key-local-windows",
        )


def _validate_key_local_distinct_on(
    scopes: tuple[Scope, ...],
    *,
    lineage: ColumnLineage,
    source_nodes: dict[str, ColumnNode],
    keys: tuple[str, ...],
    output_names: dict[int, frozenset[str]],
) -> None:
    for scope in scopes:
        if not isinstance(scope.expression, exp.Select):
            continue
        distinct = scope.expression.args.get("distinct")
        if not isinstance(distinct, exp.Distinct):
            continue
        on = distinct.args.get("on")
        if on is None and not distinct.expressions:
            continue
        partition_expressions = tuple(
            on.expressions if isinstance(on, exp.Tuple) else distinct.expressions
        )
        partition_nodes = tuple(
            node
            for expression in partition_expressions
            if (
                node := _scope_column_node(
                    scope,
                    expression,
                    lineage=lineage,
                    output_names=output_names,
                )
            )
            is not None
        )
        if len(partition_nodes) != len(keys) or any(
            sum(
                lineage.connected(source_nodes[key.lower()], node)
                for node in partition_nodes
            )
            != 1
            for key in keys
        ):
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "DISTINCT ON must partition exactly by the stable key.",
                sql_fragment=distinct.sql(dialect="duckdb"),
                documentation_anchor="key-local-distinct",
            )
        if any(
            _star_expression(selection) is not None
            for selection in scope.expression.expressions
        ):
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "DISTINCT ON wildcard output requires source schema metadata.",
                sql_fragment=scope.expression.sql(dialect="duckdb"),
                documentation_anchor="key-local-distinct",
            )
        ordered_expressions = _resolved_order_expressions(scope.expression)
        ordered_nodes = _direct_order_nodes(
            scope,
            ordered_expressions,
            lineage=lineage,
            output_names=output_names,
        )
        for selection in scope.expression.expressions:
            projected = (
                selection.this
                if isinstance(selection, exp.Alias)
                else selection
            )
            if _expression_is_order_determined(
                scope,
                projected,
                ordered_expressions=ordered_expressions,
                ordered_nodes=ordered_nodes,
                lineage=lineage,
                source_nodes=source_nodes,
                keys=keys,
                output_names=output_names,
            ):
                continue
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "DISTINCT ON ordering must determine every projected value.",
                sql_fragment=selection.sql(dialect="duckdb"),
                documentation_anchor="key-local-distinct",
            )


def _direct_order_nodes(
    scope: Scope,
    ordered_expressions: tuple[exp.Expression, ...],
    *,
    lineage: ColumnLineage,
    output_names: dict[int, frozenset[str]],
) -> tuple[ColumnNode, ...]:
    return tuple(
        node
        for expression in ordered_expressions
        if (
            node := _scope_column_node(
                scope,
                expression,
                lineage=lineage,
                output_names=output_names,
            )
        )
        is not None
    )


def _expression_is_order_determined(
    scope: Scope,
    expression: exp.Expression,
    *,
    ordered_expressions: tuple[exp.Expression, ...],
    ordered_nodes: tuple[ColumnNode, ...],
    lineage: ColumnLineage,
    source_nodes: dict[str, ColumnNode],
    keys: tuple[str, ...],
    output_names: dict[int, frozenset[str]],
) -> bool:
    columns = tuple(expression.find_all(exp.Column))
    if not columns:
        return True
    expression_node = _scope_column_node(
        scope,
        expression,
        lineage=lineage,
        output_names=output_names,
    )
    if expression_node is not None and any(
        lineage.connected(source_nodes[key.lower()], expression_node)
        for key in keys
    ):
        return True
    if any(expression == ordered for ordered in ordered_expressions):
        return True
    return all(
        (
            column_node := _scope_column_node(
                scope,
                column,
                lineage=lineage,
                output_names=output_names,
            )
        )
        is not None
        and any(
            lineage.connected(column_node, ordered_node)
            for ordered_node in ordered_nodes
        )
        for column in columns
    )


def _resolved_order_expressions(query: exp.Select) -> tuple[exp.Expression, ...]:
    order = query.args.get("order")
    if not isinstance(order, exp.Order):
        return ()
    projections = tuple(
        selection.this if isinstance(selection, exp.Alias) else selection
        for selection in query.expressions
    )
    aliases = {
        selection.alias_or_name.lower(): projected
        for selection, projected in zip(
            query.expressions,
            projections,
            strict=True,
        )
        if selection.alias_or_name
    }
    resolved: list[exp.Expression] = []
    for ordered in order.expressions:
        expression = ordered.this
        if (
            isinstance(expression, exp.Literal)
            and expression.is_int
            and 1 <= int(expression.this) <= len(projections)
        ):
            expression = projections[int(expression.this) - 1]
        elif (
            isinstance(expression, exp.Column)
            and not expression.table
            and expression.name.lower() in aliases
        ):
            expression = aliases[expression.name.lower()]
        resolved.append(expression)
    return tuple(resolved)


def _scope_column_node(
    scope: Scope,
    expression: exp.Expression,
    *,
    lineage: ColumnLineage,
    output_names: dict[int, frozenset[str]],
) -> ColumnNode | None:
    if not isinstance(expression, exp.Column):
        return None
    if expression.table:
        return _boundary_node(
            scope,
            expression.table,
            expression.name,
            lineage=lineage,
            output_names=output_names,
        )
    if len(scope.selected_sources) == 1:
        return _boundary_node(
            scope,
            next(iter(scope.selected_sources)),
            expression.name,
            lineage=lineage,
            output_names=output_names,
        )
    output_node = (id(scope), "$output", expression.name.lower())
    return output_node if output_node in lineage.nodes else None


def _scope_windows(scope: Scope):
    for window in scope.expression.find_all(exp.Window):
        owner = window.find_ancestor(exp.Select)
        if owner is scope.expression:
            yield window


def _scope_aggregates(scope: Scope):
    for aggregate in scope.expression.find_all(exp.AggFunc):
        if isinstance(aggregate.parent, exp.Window):
            continue
        owner = aggregate.find_ancestor(exp.Select)
        if owner is scope.expression:
            yield aggregate


def _validate_functions(query: exp.Query) -> None:
    for function in query.find_all(exp.Func):
        rendered = function.sql(dialect="duckdb")
        if isinstance(function, (exp.Explode, exp.Unnest)) and next(
            function.find_all(exp.Query), None
        ) is not None:
            _unavailable(
                OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
                "UNNEST arguments must be row-local expressions.",
                sql_fragment=rendered,
                documentation_anchor="row-local-expansion",
            )
        if (
            isinstance(function, VOLATILE_FUNCTION_TYPES)
            or function.name.lower() in VOLATILE_FUNCTION_NAMES
        ):
            _unavailable(
                OptimizationCode.NONDETERMINISTIC_FUNCTION,
                f"{rendered} is not stable across repeated execution.",
                sql_fragment=rendered,
                documentation_anchor="deterministic-functions",
            )
        if isinstance(function.parent, exp.Window):
            if isinstance(
                function,
                (
                    *_KEY_LOCAL_WINDOW_FUNCTION_TYPES,
                    *_ROW_CHOICE_WINDOW_FUNCTION_TYPES,
                ),
            ):
                continue
            _unavailable(
                OptimizationCode.UNSUPPORTED_FUNCTION,
                f"Atlas cannot prove that window function {rendered} "
                "is deterministic.",
                sql_fragment=rendered,
                documentation_anchor="key-local-windows",
            )
        if isinstance(function, exp.AggFunc):
            if isinstance(
                function,
                (*_KEYED_AGGREGATE_TYPES, *_ARG_EXTREME_AGGREGATE_TYPES),
            ) or (
                isinstance(function, _VALUE_ORDERED_AGGREGATE_TYPES)
                and _value_ordered_aggregate_is_stable(function)
            ):
                continue
            _unavailable(
                OptimizationCode.UNSUPPORTED_FUNCTION,
                f"Atlas cannot prove that aggregate {rendered} is stable.",
                sql_fragment=rendered,
                documentation_anchor="keyed-aggregation",
            )
        if not (
            isinstance(function, _DETERMINISTIC_FUNCTION_TYPES)
            or (
                isinstance(function, exp.Anonymous)
                and function.name.lower()
                in DETERMINISTIC_DUCKDB_FUNCTION_NAMES
            )
        ):
            _unavailable(
                OptimizationCode.UNSUPPORTED_FUNCTION,
                f"Atlas cannot prove that {rendered} is a deterministic "
                "row-local function.",
                sql_fragment=rendered,
                documentation_anchor="deterministic-functions",
            )


def _value_ordered_aggregate_is_stable(function: exp.AggFunc) -> bool:
    order = function.this
    if not isinstance(order, exp.Order):
        return False
    value = order.this
    if isinstance(value, exp.Distinct):
        if len(value.expressions) != 1:
            return False
        value = value.expressions[0]
    return any(
        isinstance(ordered, exp.Ordered) and ordered.this == value
        for ordered in order.expressions
    )


def _conjuncts(expression: exp.Expression) -> tuple[exp.Expression, ...]:
    if isinstance(expression, exp.Paren):
        return _conjuncts(expression.this)
    if isinstance(expression, exp.And):
        return (*_conjuncts(expression.this), *_conjuncts(expression.expression))
    return (expression,)


def _identifier(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not _SAFE_IDENTIFIER.fullmatch(normalized):
        _unavailable(
            OptimizationCode.UNSUPPORTED_QUERY_SHAPE,
            f"{label} must be an unqualified DuckDB identifier.",
            sql_fragment=value,
            documentation_anchor="query-boundary",
        )
    return normalized


def _sql_fragment(value: object) -> str:
    if isinstance(value, exp.Expression):
        return value.sql(dialect="duckdb")
    if isinstance(value, list):
        return " ".join(
            item.sql(dialect="duckdb")
            if isinstance(item, exp.Expression)
            else str(item)
            for item in value
        )
    return str(value)


def _unavailable(
    code: OptimizationCode,
    message: str,
    *,
    sql_fragment: str | None = None,
    documentation_anchor: str | None = None,
    cause: Exception | None = None,
) -> None:
    error = QueryOptimizationUnavailable(
        OptimizationDiagnostic(
            code=code,
            message=message,
            sql_fragment=sql_fragment,
            documentation_anchor=documentation_anchor,
        )
    )
    if cause is None:
        raise error
    raise error from cause
