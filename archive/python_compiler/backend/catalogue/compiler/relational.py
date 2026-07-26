"""Purpose-neutral relational facts derived from one SQLGlot scope graph."""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp
from sqlglot.optimizer.scope import Scope

ColumnNode = tuple[int, str, str]


class ColumnLineage:
    """Union-find graph used while proving equivalent column identities."""

    def __init__(
        self,
        components: tuple[frozenset[ColumnNode], ...] = (),
    ) -> None:
        self._parent: dict[ColumnNode, ColumnNode] = {}
        for component in components:
            nodes = tuple(component)
            if not nodes:
                continue
            self.add(nodes[0])
            for node in nodes[1:]:
                self.union(nodes[0], node)

    @property
    def nodes(self) -> tuple[ColumnNode, ...]:
        return tuple(self._parent)

    def add(self, node: ColumnNode) -> None:
        self._parent.setdefault(node, node)

    def find(self, node: ColumnNode) -> ColumnNode:
        self.add(node)
        parent = self._parent[node]
        if parent != node:
            self._parent[node] = self.find(parent)
        return self._parent[node]

    def union(self, left: ColumnNode, right: ColumnNode) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root

    def connected(self, left: ColumnNode, right: ColumnNode) -> bool:
        return self.find(left) == self.find(right)

    def freeze(self) -> "FrozenColumnLineage":
        by_root: dict[ColumnNode, set[ColumnNode]] = {}
        for node in self.nodes:
            by_root.setdefault(self.find(node), set()).add(node)
        components = tuple(
            sorted(
                (frozenset(component) for component in by_root.values()),
                key=lambda component: tuple(sorted(component)),
            )
        )
        return FrozenColumnLineage(components=components)


@dataclass(frozen=True, slots=True)
class FrozenColumnLineage:
    components: tuple[frozenset[ColumnNode], ...]

    @property
    def nodes(self) -> tuple[ColumnNode, ...]:
        return tuple(node for component in self.components for node in component)

    def connected(self, left: ColumnNode, right: ColumnNode) -> bool:
        return any(
            left in component and right in component
            for component in self.components
        )


@dataclass(frozen=True, order=True, slots=True)
class RelationColumn:
    relation: str
    column: str


@dataclass(frozen=True, slots=True)
class DirectProjection:
    output: str
    source: RelationColumn


@dataclass(frozen=True, slots=True)
class ScopeRelationalFacts:
    """Immutable lineage, equivalence, and nullability facts for one scope."""

    scope_id: int
    direct_projections: tuple[DirectProjection, ...]
    equivalence_classes: tuple[frozenset[RelationColumn], ...]
    nullable_relations: frozenset[str]
    full_join_nullable_relations: frozenset[str]

    def equivalents(self, column: RelationColumn) -> frozenset[RelationColumn]:
        for component in self.equivalence_classes:
            if column in component:
                return component
        return frozenset()


def analyze_scope_relations(scope: Scope) -> ScopeRelationalFacts:
    if not isinstance(scope.expression, exp.Select):
        return ScopeRelationalFacts(
            scope_id=id(scope),
            direct_projections=(),
            equivalence_classes=(),
            nullable_relations=frozenset(),
            full_join_nullable_relations=frozenset(),
        )

    direct_projections: list[DirectProjection] = []
    projection_sources = (
        scope.selected_sources
        if scope.selected_sources
        else scope.sources
    )
    only_source = (
        next(iter(projection_sources))
        if len(projection_sources) == 1
        else None
    )
    for selection in scope.expression.expressions:
        column = _direct_column(selection)
        relation = column.table if column is not None else ""
        if column is not None and not relation and only_source is not None:
            relation = only_source
        if (
            column is not None
            and selection.alias_or_name
            and relation
        ):
            direct_projections.append(
                DirectProjection(
                    output=selection.alias_or_name.lower(),
                    source=RelationColumn(
                        relation=relation.lower(),
                        column=column.name.lower(),
                    ),
                )
            )
    graph: dict[RelationColumn, set[RelationColumn]] = {}
    nullable: set[str] = set()
    full_join_nullable: set[str] = set()
    from_clause = scope.expression.args.get("from_")
    available = []
    if from_clause is not None and from_clause.this is not None:
        available.append(from_clause.this.alias_or_name.lower())

    for join in scope.expression.args.get("joins") or ():
        side = str(join.args.get("side") or "").upper()
        kind = str(join.args.get("kind") or "").upper()
        right_alias = join.this.alias_or_name.lower()
        inner = not side and kind in {"", "INNER"}
        if side == "LEFT":
            nullable.add(right_alias)
        elif side == "RIGHT":
            nullable.update(available)
        elif side == "FULL":
            affected = {*available, right_alias}
            nullable.update(affected)
            full_join_nullable.update(affected)

        if inner:
            for identifier in join.args.get("using") or ():
                if not isinstance(identifier, exp.Identifier):
                    continue
                right = RelationColumn(right_alias, identifier.name.lower())
                for left_alias in available:
                    _connect(
                        graph,
                        RelationColumn(left_alias, identifier.name.lower()),
                        right,
                    )
            condition = join.args.get("on")
            for predicate in _conjuncts(condition) if condition else ():
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
                    _connect(
                        graph,
                        RelationColumn(
                            left.table.lower(),
                            left.name.lower(),
                        ),
                        RelationColumn(
                            right.table.lower(),
                            right.name.lower(),
                        ),
                    )
        available.append(right_alias)

    where = scope.expression.args.get("where")
    for predicate in _conjuncts(where.this) if isinstance(where, exp.Where) else ():
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
            _connect(
                graph,
                RelationColumn(
                    left.table.lower(),
                    left.name.lower(),
                ),
                RelationColumn(
                    right.table.lower(),
                    right.name.lower(),
                ),
            )

    components: list[frozenset[RelationColumn]] = []
    pending = set(graph)
    while pending:
        component = frozenset(_reachable(graph, next(iter(pending))))
        components.append(component)
        pending.difference_update(component)
    components.sort(key=lambda item: tuple(sorted(item)))
    return ScopeRelationalFacts(
        scope_id=id(scope),
        direct_projections=tuple(direct_projections),
        equivalence_classes=tuple(components),
        nullable_relations=frozenset(nullable),
        full_join_nullable_relations=frozenset(full_join_nullable),
    )


def analyze_query_lineage(
    scopes: tuple[Scope, ...],
    facts: tuple[ScopeRelationalFacts, ...],
) -> FrozenColumnLineage:
    """Build direct cross-scope lineage without purpose-specific key rules."""

    lineage = ColumnLineage()
    facts_by_scope = {item.scope_id: item for item in facts}
    for scope in scopes:
        scope_facts = facts_by_scope[id(scope)]
        for component in scope_facts.equivalence_classes:
            nodes = tuple(
                (
                    id(_relation_owner_scope(scope, column.relation)),
                    column.relation,
                    column.column,
                )
                for column in component
            )
            if nodes:
                lineage.add(nodes[0])
                for node in nodes[1:]:
                    lineage.union(nodes[0], node)
        for projection in scope_facts.direct_projections:
            output = (id(scope), "$output", projection.output)
            source = scope.sources.get(projection.source.relation)
            if isinstance(source, Scope):
                input_node = (
                    id(source),
                    "$output",
                    projection.source.column,
                )
            elif (
                source is None
                and scope.is_correlated_subquery
            ):
                owner = _relation_owner_scope(
                    scope,
                    projection.source.relation,
                )
                input_node = (
                    id(owner),
                    projection.source.relation,
                    projection.source.column,
                )
            else:
                input_node = (
                    id(scope),
                    projection.source.relation,
                    projection.source.column,
                )
            lineage.union(output, input_node)
    return lineage.freeze()


def _relation_owner_scope(scope: Scope, relation: str) -> Scope:
    owner: Scope | None = scope
    while owner is not None:
        if relation in owner.sources:
            return owner
        owner = owner.parent
    return scope


def _direct_column(expression: exp.Expression) -> exp.Column | None:
    candidate = expression.this if isinstance(expression, exp.Alias) else expression
    return candidate if isinstance(candidate, exp.Column) else None


def _conjuncts(expression: exp.Expression) -> tuple[exp.Expression, ...]:
    if isinstance(expression, exp.Paren):
        return _conjuncts(expression.this)
    if isinstance(expression, exp.And):
        return (*_conjuncts(expression.this), *_conjuncts(expression.expression))
    return (expression,)


def _connect(
    graph: dict[RelationColumn, set[RelationColumn]],
    left: RelationColumn,
    right: RelationColumn,
) -> None:
    graph.setdefault(left, set()).add(right)
    graph.setdefault(right, set()).add(left)


def _reachable(
    graph: dict[RelationColumn, set[RelationColumn]],
    start: RelationColumn,
) -> set[RelationColumn]:
    reached = {start}
    pending = [start]
    while pending:
        node = pending.pop()
        for neighbor in graph.get(node, ()):
            if neighbor not in reached:
                reached.add(neighbor)
                pending.append(neighbor)
    return reached
