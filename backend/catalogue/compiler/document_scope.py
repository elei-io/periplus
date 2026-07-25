"""Prove and render bounded interactive scans of the managed DOM relation."""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp
from sqlglot.optimizer.scope import Scope

from .analysis import AnalyzedCatalogueQuery, analyze_resolved_query
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
from .metadata import (
    BoundParameter,
    CatalogueMetadataSnapshot,
    ManagedTableMetadata,
)
from .metadata_aggregates import metadata_only_row_count_table
from .relational import RelationColumn
from .streaming_limit import direct_streaming_limit
from .syntax import classify_select

SCOPED_ELEMENTS_RELATION = "_atlas_interactive_scoped_elements"
_DOCUMENT_ID = "document_id"


@dataclass(frozen=True, slots=True)
class DocumentScopeCompilation:
    """Executable SQL plus the runtime work needed to hydrate its DOM input."""

    sql: str
    scope_sql: str
    element_columns: tuple[str, ...]


def compile_document_scope(
    sql: str,
    *,
    metadata: CatalogueMetadataSnapshot,
    maximum_documents: int,
    maximum_unscoped_element_rows: int,
    bound_parameters: tuple[BoundParameter, ...] = (),
) -> DocumentScopeCompilation | None:
    """Replace derived elements scans after proving a selective document source."""

    elements = metadata.table("elements", schema_name="main")
    documents = metadata.table("documents", schema_name="main")
    if (
        elements is None
        or documents is None
        or not _targets_documents(elements)
    ):
        return None

    query = classify_select(sql)
    analysis = analyze_resolved_query(query)
    scans = _element_scans(analysis, elements)
    if not scans:
        return None
    metadata_count_table = metadata_only_row_count_table(query)
    if (
        metadata_count_table is not None
        and _is_table(metadata_count_table, elements)
    ):
        return None
    streaming_limit = direct_streaming_limit(
        query,
        bound_parameters={
            parameter.name: parameter.value
            for parameter in bound_parameters
        },
    )
    if (
        streaming_limit is not None
        and _is_table(streaming_limit.table, elements)
        and len(scans) == 1
        and streaming_limit.maximum_rows_read
        <= maximum_unscoped_element_rows
    ):
        return None
    if all(
        _has_literal_document_scope(scope, alias)
        for scope, alias in scans
    ):
        return None
    if all(_has_statically_empty_filter(scope) for scope, _ in scans):
        return None

    anchors: dict[str, exp.Query] = {}
    for scope, alias in scans:
        if _has_literal_document_scope(scope, alias):
            literal_anchor = _literal_document_anchor(scope, alias=alias)
            if literal_anchor is None:
                _unbounded()
            rendered = literal_anchor.sql(dialect="duckdb", pretty=True)
            anchors.setdefault(rendered, literal_anchor)
            continue
        anchor = _find_anchor(
            analysis,
            scope=scope,
            element_alias=alias,
            metadata=metadata,
            elements=elements,
        )
        if anchor is None:
            _unbounded()
        rendered = anchor.sql(dialect="duckdb", pretty=True)
        anchors.setdefault(rendered, anchor)

    if not anchors:
        return None
    scope_sql = _render_scope_sql(
        tuple(anchors.values()),
        maximum_documents=maximum_documents,
    )
    columns = _element_columns(analysis, scans)
    rewritten = _replace_elements(query, elements=elements)
    return DocumentScopeCompilation(
        sql=rewritten.sql(dialect="duckdb", pretty=True),
        scope_sql=scope_sql,
        element_columns=columns,
    )


def _element_scans(
    analysis: AnalyzedCatalogueQuery,
    elements: ManagedTableMetadata,
) -> tuple[tuple[object, str], ...]:
    found: list[tuple[object, str]] = []
    for scope in analysis.scopes:
        for alias, (_, source) in scope.selected_sources.items():
            if isinstance(source, exp.Table) and _is_table(source, elements):
                found.append((scope, alias.lower()))
    return tuple(found)


def _has_literal_document_scope(scope, alias: str) -> bool:
    expression = scope.expression
    if not isinstance(expression, exp.Select):
        return False
    predicates: list[exp.Expression] = []
    where = expression.args.get("where")
    if isinstance(where, exp.Where):
        predicates.extend(_conjuncts(where.this))
    for join in expression.args.get("joins") or ():
        condition = join.args.get("on")
        if condition is not None:
            predicates.extend(_conjuncts(condition))
    return any(
        _literal_document_predicate(item, alias=alias)
        for item in predicates
    )


def _has_statically_empty_filter(scope) -> bool:
    expression = scope.expression
    if not isinstance(expression, exp.Select):
        return False
    where = expression.args.get("where")
    return isinstance(where, exp.Where) and any(
        isinstance(predicate, exp.Boolean) and not predicate.this
        for predicate in _conjuncts(where.this)
    )


def _literal_document_predicate(
    predicate: exp.Expression,
    *,
    alias: str,
) -> bool:
    if isinstance(predicate, exp.Paren):
        return _literal_document_predicate(predicate.this, alias=alias)
    if isinstance(predicate, exp.And):
        return _literal_document_predicate(
            predicate.this,
            alias=alias,
        ) or _literal_document_predicate(
            predicate.expression,
            alias=alias,
        )
    if isinstance(predicate, exp.Or):
        return _literal_document_predicate(
            predicate.this,
            alias=alias,
        ) and _literal_document_predicate(
            predicate.expression,
            alias=alias,
        )
    if isinstance(predicate, (exp.EQ, exp.NullSafeEQ)):
        return (
            _is_document_column(predicate.this, alias=alias)
            and _is_static_value(predicate.expression)
        ) or (
            _is_document_column(predicate.expression, alias=alias)
            and _is_static_value(predicate.this)
        )
    if isinstance(predicate, exp.In) and _is_document_column(
        predicate.this,
        alias=alias,
    ):
        values = tuple(predicate.expressions)
        return bool(values) and all(_is_static_value(value) for value in values)
    return False


def _literal_document_anchor(scope, *, alias: str) -> exp.Query | None:
    expression = scope.expression
    if not isinstance(expression, exp.Select):
        return None
    predicates: list[exp.Expression] = []
    where = expression.args.get("where")
    if isinstance(where, exp.Where):
        predicates.extend(_conjuncts(where.this))
    for join in expression.args.get("joins") or ():
        condition = join.args.get("on")
        if condition is not None:
            predicates.extend(_conjuncts(condition))
    values: list[exp.Expression] = []
    for predicate in predicates:
        values.extend(
            value.copy()
            for value in _literal_document_values(
                predicate,
                alias=alias,
            )
        )
    if not values:
        return None
    tuples = [exp.Tuple(expressions=[value]) for value in values]
    return exp.select(_DOCUMENT_ID).from_(
        exp.Values(
            expressions=tuples,
            alias=exp.TableAlias(
                this=exp.to_identifier("_atlas_literal_documents"),
                columns=[exp.to_identifier(_DOCUMENT_ID)],
            ),
        )
    )


def _literal_document_values(
    predicate: exp.Expression,
    *,
    alias: str,
) -> tuple[exp.Expression, ...]:
    if isinstance(predicate, exp.Paren):
        return _literal_document_values(predicate.this, alias=alias)
    if isinstance(predicate, (exp.And, exp.Or)):
        left = _literal_document_values(predicate.this, alias=alias)
        right = _literal_document_values(
            predicate.expression,
            alias=alias,
        )
        if isinstance(predicate, exp.Or) and (not left or not right):
            return ()
        return (*left, *right)
    if isinstance(predicate, (exp.EQ, exp.NullSafeEQ)):
        if (
            _is_document_column(predicate.this, alias=alias)
            and _is_literal_value(predicate.expression)
        ):
            return (predicate.expression,)
        if (
            _is_document_column(predicate.expression, alias=alias)
            and _is_literal_value(predicate.this)
        ):
            return (predicate.this,)
        return ()
    if (
        isinstance(predicate, exp.In)
        and _is_document_column(predicate.this, alias=alias)
        and predicate.expressions
        and all(_is_literal_value(value) for value in predicate.expressions)
    ):
        return tuple(predicate.expressions)
    return ()


def _is_document_column(
    expression: exp.Expression,
    *,
    alias: str,
) -> bool:
    return (
        isinstance(expression, exp.Column)
        and expression.name.lower() == _DOCUMENT_ID
        and (not expression.table or expression.table.lower() == alias)
    )


def _is_static_value(expression: exp.Expression) -> bool:
    return isinstance(expression, (exp.Literal, exp.Placeholder)) or (
        isinstance(expression, exp.Cast)
        and isinstance(expression.this, exp.Literal)
    )


def _is_literal_value(expression: exp.Expression) -> bool:
    return isinstance(expression, exp.Literal) or (
        isinstance(expression, exp.Cast)
        and isinstance(expression.this, exp.Literal)
    )


def _find_anchor(
    analysis: AnalyzedCatalogueQuery,
    *,
    scope,
    element_alias: str,
    metadata: CatalogueMetadataSnapshot,
    elements: ManagedTableMetadata,
) -> exp.Query | None:
    predicate_anchor = _find_predicate_anchor(
        analysis,
        scope=scope,
        element_alias=element_alias,
        metadata=metadata,
        elements=elements,
    )
    if predicate_anchor is not None:
        return predicate_anchor
    facts = analysis.facts_for(scope)
    equivalents = facts.equivalents(
        RelationColumn(element_alias, _DOCUMENT_ID)
    )
    for candidate in sorted(equivalents):
        if candidate.relation == element_alias:
            continue
        anchor = _trace_source(
            analysis,
            scope,
            relation=candidate.relation,
            column=candidate.column,
            metadata=metadata,
            elements=elements,
            visited=set(),
        )
        if anchor is not None:
            return anchor
    return None


def _find_predicate_anchor(
    analysis: AnalyzedCatalogueQuery,
    *,
    scope,
    element_alias: str,
    metadata: CatalogueMetadataSnapshot,
    elements: ManagedTableMetadata,
) -> exp.Query | None:
    expression = scope.expression
    if not isinstance(expression, exp.Select):
        return None
    where = expression.args.get("where")
    if not isinstance(where, exp.Where):
        return None
    for predicate in _conjuncts(where.this):
        if (
            isinstance(predicate, exp.In)
            and _is_document_column(predicate.this, alias=element_alias)
        ):
            query = predicate.args.get("query")
            if not isinstance(query, exp.Subquery):
                continue
            inner_scope = _scope_for_expression(analysis, query.this)
            if inner_scope is None:
                continue
            projections = analysis.facts_for(inner_scope).direct_projections
            if len(projections) != 1:
                continue
            projection = projections[0]
            anchor = _trace_source(
                analysis,
                inner_scope,
                relation=projection.source.relation,
                column=projection.source.column,
                metadata=metadata,
                elements=elements,
                visited=set(),
            )
            if anchor is not None:
                return anchor
        if isinstance(predicate, exp.Exists):
            inner_scope = _scope_for_expression(analysis, predicate.this)
            if (
                inner_scope is None
                or element_alias in inner_scope.sources
            ):
                continue
            candidates = _correlated_document_sources(
                inner_scope,
                element_alias=element_alias,
            )
            for candidate in candidates:
                anchor = _trace_source(
                    analysis,
                    inner_scope,
                    relation=candidate.relation,
                    column=candidate.column,
                    metadata=metadata,
                    elements=elements,
                    visited=set(),
                )
                if anchor is not None:
                    return anchor
    return None


def _correlated_document_sources(
    inner_scope,
    *,
    element_alias: str,
) -> tuple[RelationColumn, ...]:
    expression = inner_scope.expression
    if not isinstance(expression, exp.Select):
        return ()
    where = expression.args.get("where")
    if not isinstance(where, exp.Where):
        return ()
    found: set[RelationColumn] = set()
    for predicate in _conjuncts(where.this):
        if not isinstance(predicate, (exp.EQ, exp.NullSafeEQ)):
            continue
        for outer, inner in (
            (predicate.this, predicate.expression),
            (predicate.expression, predicate.this),
        ):
            if (
                not isinstance(outer, exp.Column)
                or outer.name.lower() != _DOCUMENT_ID
                or outer.table.lower() != element_alias
                or not isinstance(inner, exp.Column)
                or not inner.table
                or inner.table.lower() not in inner_scope.sources
            ):
                continue
            found.add(
                RelationColumn(
                    inner.table.lower(),
                    inner.name.lower(),
                )
            )
    return tuple(sorted(found))


def _scope_for_expression(
    analysis: AnalyzedCatalogueQuery,
    expression: exp.Expression,
):
    return next(
        (
            candidate
            for candidate in analysis.scopes
            if candidate.expression is expression
        ),
        None,
    )


def _trace_source(
    analysis: AnalyzedCatalogueQuery,
    scope,
    *,
    relation: str,
    column: str,
    metadata: CatalogueMetadataSnapshot,
    elements: ManagedTableMetadata,
    visited: set[tuple[int, str, str]],
) -> exp.Query | None:
    marker = (id(scope), relation, column)
    if marker in visited:
        return None
    visited.add(marker)
    source = scope.sources.get(relation)
    if source is None:
        return None
    if isinstance(source, exp.Table):
        table = _metadata_table(source, metadata)
        if (
            table is None
            or table.table_uuid == elements.table_uuid
            or not _document_key_source(table, column=column)
        ):
            return None
        predicates = _relation_predicates(scope, relation=relation)
        if not predicates:
            return None
        table_copy = source.copy()
        if table_copy.args.get("alias") is None:
            table_copy.set(
                "alias",
                exp.TableAlias(this=exp.to_identifier(relation)),
            )
        selected = exp.select(
            exp.alias_(
                exp.column(column, table=relation),
                _DOCUMENT_ID,
            )
        ).from_(table_copy)
        combined = predicates[0].copy()
        for predicate in predicates[1:]:
            combined = exp.and_(combined, predicate.copy())
        selected.set("where", exp.Where(this=combined))
        return selected

    if not isinstance(source, Scope):
        return None
    if isinstance(source.expression, exp.Values):
        return _values_document_anchor(source.expression, column=column)
    projection = next(
        (
            item
            for item in analysis.facts_for(source).direct_projections
            if item.output == column.lower()
        ),
        None,
    )
    if projection is None:
        return None
    return _trace_source(
        analysis,
        source,
        relation=projection.source.relation,
        column=projection.source.column,
        metadata=metadata,
        elements=elements,
        visited=visited,
    )


def _values_document_anchor(
    values: exp.Values,
    *,
    column: str,
) -> exp.Query | None:
    alias = values.args.get("alias")
    if not isinstance(alias, exp.TableAlias) or not alias.name:
        return None
    columns = tuple(alias.args.get("columns") or ())
    position = next(
        (
            index
            for index, candidate in enumerate(columns)
            if candidate.name.lower() == column.lower()
        ),
        None,
    )
    if position is None:
        return None
    selected_values: list[exp.Expression] = []
    for row in values.expressions:
        if (
            not isinstance(row, exp.Tuple)
            or position >= len(row.expressions)
            or not _is_literal_value(row.expressions[position])
        ):
            return None
        selected_values.append(row.expressions[position].copy())
    if not selected_values:
        return None
    literal_values = exp.Values(
        expressions=[
            exp.Tuple(expressions=[value])
            for value in selected_values
        ],
        alias=exp.TableAlias(
            this=exp.to_identifier("_atlas_values_documents"),
            columns=[exp.to_identifier(_DOCUMENT_ID)],
        ),
    )
    return exp.select(_DOCUMENT_ID).from_(literal_values)


def _relation_predicates(
    scope,
    *,
    relation: str,
) -> tuple[exp.Expression, ...]:
    expression = scope.expression
    if not isinstance(expression, exp.Select):
        return ()
    where = expression.args.get("where")
    if not isinstance(where, exp.Where):
        return ()
    allow_unqualified = len(scope.selected_sources) == 1
    return tuple(
        predicate
        for predicate in _conjuncts(where.this)
        if (columns := tuple(predicate.find_all(exp.Column)))
        and all(
            (
                column.table.lower() == relation
                if column.table
                else allow_unqualified
            )
            for column in columns
        )
        and next(predicate.find_all((exp.Query, exp.Window)), None) is None
        and _repeatable_expression(predicate)
    )


def _repeatable_expression(expression: exp.Expression) -> bool:
    for function in expression.find_all(exp.Func):
        if (
            isinstance(function, VOLATILE_FUNCTION_TYPES)
            or function.name.lower() in VOLATILE_FUNCTION_NAMES
        ):
            return False
        if isinstance(function, exp.AggFunc) or isinstance(
            function.parent,
            exp.Window,
        ):
            continue
        if isinstance(function, exp.Anonymous):
            if function.name.lower() not in DETERMINISTIC_DUCKDB_FUNCTION_NAMES:
                return False
            continue
        if not isinstance(
            function,
            DETERMINISTIC_ROW_LOCAL_FUNCTION_TYPES,
        ):
            return False
    return True


def _render_scope_sql(
    anchors: tuple[exp.Query, ...],
    *,
    maximum_documents: int,
) -> str:
    branches = [
        "SELECT document_id FROM (\n"
        + anchor.sql(dialect="duckdb", pretty=True)
        + "\n) AS _atlas_document_source"
        for anchor in anchors
    ]
    candidates = "\nUNION\n".join(branches)
    return (
        "WITH _atlas_document_candidates AS (\n"
        f"{candidates}\n"
        ")\n"
        "SELECT DISTINCT candidate.document_id\n"
        "FROM _atlas_document_candidates AS candidate\n"
        "WHERE candidate.document_id IS NOT NULL\n"
        f"LIMIT {maximum_documents + 1}"
    )


def _element_columns(
    analysis: AnalyzedCatalogueQuery,
    scans: tuple[tuple[object, str], ...],
) -> tuple[str, ...]:
    columns: list[str] = [_DOCUMENT_ID]
    seen = {_DOCUMENT_ID}
    for scope, alias in scans:
        expression = scope.expression
        if not isinstance(expression, exp.Select):
            continue
        for column in _local_columns(expression):
            if column.is_star and (
                not column.table or column.table.lower() == alias
            ):
                return ("*",)
            if not column.table and column.name.lower() == alias:
                return ("*",)
            if (
                column.name
                and column.name.lower() not in seen
                and (
                    column.table.lower() == alias
                    or (
                        not column.table
                        and len(scope.selected_sources) == 1
                    )
                )
            ):
                seen.add(column.name.lower())
                columns.append(column.name)
    return tuple(columns)


def _local_columns(expression: exp.Expression) -> tuple[exp.Column, ...]:
    found: list[exp.Column] = []

    def visit(node: exp.Expression, *, root: bool = False) -> None:
        if not root and isinstance(node, exp.Query):
            return
        if isinstance(node, exp.Column):
            found.append(node)
        for child in node.iter_expressions():
            visit(child)

    visit(expression, root=True)
    return tuple(found)


def _replace_elements(
    query: exp.Query,
    *,
    elements: ManagedTableMetadata,
) -> exp.Query:
    rewritten = query.copy()
    cte_names = {
        cte.alias_or_name.lower()
        for cte in rewritten.find_all(exp.CTE)
        if cte.alias_or_name
    }

    def replace(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Table) or not _is_table(node, elements):
            return node
        if not node.db and node.name.lower() in cte_names:
            return node
        return exp.Table(
            this=exp.to_identifier(SCOPED_ELEMENTS_RELATION),
            alias=(
                node.args["alias"].copy()
                if node.args.get("alias") is not None
                else None
            ),
        )

    return rewritten.transform(replace)


def _metadata_table(
    table: exp.Table,
    metadata: CatalogueMetadataSnapshot,
) -> ManagedTableMetadata | None:
    schema = table.db or None
    return metadata.table(table.name, schema_name=schema)


def _is_table(
    table: exp.Table,
    metadata: ManagedTableMetadata,
) -> bool:
    return (
        isinstance(table.this, exp.Identifier)
        and table.name.lower() == metadata.table_name.lower()
        and (
            not table.db
            or table.db.lower() == metadata.schema_name.lower()
        )
    )


def _targets_documents(table: ManagedTableMetadata) -> bool:
    return any(
        tuple(item.lower() for item in relationship.columns)
        == (_DOCUMENT_ID,)
        and relationship.target_table.lower() == "documents"
        and tuple(item.lower() for item in relationship.target_columns)
        == (_DOCUMENT_ID,)
        for relationship in table.relationships
    )


def _document_key_source(
    table: ManagedTableMetadata,
    *,
    column: str,
) -> bool:
    if (
        table.table_name.lower() == "documents"
        and column.lower() in {item.lower() for item in table.stable_key}
    ):
        return True
    return any(
        tuple(item.lower() for item in relationship.columns)
        == (column.lower(),)
        and relationship.target_table.lower() == "documents"
        and tuple(item.lower() for item in relationship.target_columns)
        == (_DOCUMENT_ID,)
        for relationship in table.relationships
    )


def _conjuncts(expression: exp.Expression) -> tuple[exp.Expression, ...]:
    if isinstance(expression, exp.Paren):
        return _conjuncts(expression.this)
    if isinstance(expression, exp.And):
        return (*_conjuncts(expression.this), *_conjuncts(expression.expression))
    return (expression,)


def _unbounded() -> None:
    raise QueryOptimizationUnavailable(
        OptimizationDiagnostic(
            code=OptimizationCode.UNBOUNDED_RELATION,
            message=(
                "Interactive elements queries must constrain document_id "
                "directly or derive it through selective managed "
                "crawl/document lineage, unless the query is a direct "
                "unfiltered row count or a bounded direct streaming scan."
            ),
            documentation_anchor="bounded-document-scans",
        )
    )
