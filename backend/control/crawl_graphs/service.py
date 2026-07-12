"""Postgres-backed crawl-graph definitions and frozen execution snapshots."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlglot import exp

from repository.catalogue.query import CatalogueQueryError, classify_select

from .models import CrawlGraph, CrawlGraphEdge, CrawlGraphNode
from .schemas import (
    CrawlGraphCreate,
    CrawlGraphDetail,
    CrawlGraphEdgeCreate,
    CrawlGraphEdgeRecord,
    CrawlGraphEdgeUpdate,
    CrawlGraphNodeCreate,
    CrawlGraphNodeRecord,
    CrawlGraphNodePositionUpdate,
    CrawlGraphNodeUpdate,
    CrawlGraphRecord,
    CrawlGraphUpdate,
    FrozenGraphEdge,
    FrozenGraphNode,
    FrozenGraphSnapshot,
)


class CrawlGraphNotFoundError(LookupError):
    pass


class CrawlGraphConflictError(ValueError):
    pass


class CrawlGraphValidationError(ValueError):
    pass


def _clean(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise CrawlGraphValidationError("Name must not be blank.")
    return cleaned


def _record(graph: CrawlGraph) -> CrawlGraphRecord:
    return CrawlGraphRecord.model_validate(graph, from_attributes=True)


def _node_record(node: CrawlGraphNode) -> CrawlGraphNodeRecord:
    return CrawlGraphNodeRecord.model_validate(node, from_attributes=True)


def _edge_record(edge: CrawlGraphEdge) -> CrawlGraphEdgeRecord:
    return CrawlGraphEdgeRecord.model_validate(edge, from_attributes=True)


def detail(graph: CrawlGraph) -> CrawlGraphDetail:
    return CrawlGraphDetail(
        **_record(graph).model_dump(),
        nodes=[_node_record(node) for node in sorted(graph.nodes, key=lambda item: item.created_at)],
        edges=[_edge_record(edge) for edge in sorted(graph.edges, key=lambda item: item.created_at)],
    )


def create_graph(session: Session, request: CrawlGraphCreate) -> CrawlGraphDetail:
    graph = CrawlGraph(name=_clean(request.name), description=request.description)
    session.add(graph)
    session.flush()
    return detail(graph)


def list_graphs(session: Session) -> list[CrawlGraphRecord]:
    return [_record(graph) for graph in session.scalars(select(CrawlGraph).order_by(CrawlGraph.created_at.desc()))]


def get_graph(session: Session, graph_id: UUID, *, lock: bool = False) -> CrawlGraph:
    statement = (
        select(CrawlGraph)
        .where(CrawlGraph.id == graph_id)
        .options(selectinload(CrawlGraph.nodes), selectinload(CrawlGraph.edges))
    )
    if lock:
        statement = statement.with_for_update()
    graph = session.scalar(statement)
    if graph is None:
        raise CrawlGraphNotFoundError(f"Crawl graph {graph_id} was not found.")
    return graph


def update_graph(session: Session, graph_id: UUID, request: CrawlGraphUpdate) -> CrawlGraphDetail:
    graph = get_graph(session, graph_id, lock=True)
    if request.root_node_id is not None:
        _get_node(session, graph_id, request.root_node_id)
    graph.name = _clean(request.name)
    graph.description = request.description
    graph.root_node_id = request.root_node_id
    session.flush()
    return detail(graph)


def delete_graph(session: Session, graph_id: UUID) -> None:
    graph = get_graph(session, graph_id, lock=True)
    session.delete(graph)
    session.flush()


def create_node(session: Session, graph_id: UUID, request: CrawlGraphNodeCreate) -> CrawlGraphNodeRecord:
    graph = get_graph(session, graph_id, lock=True)
    node = CrawlGraphNode(
        graph_id=graph_id,
        name=_clean(request.name),
        description=request.description,
    )
    session.add(node)
    _flush_conflict(session, "A node with this name already exists in the graph.")
    if graph.root_node_id is None:
        graph.root_node_id = node.id
        session.flush()
    return _node_record(node)


def update_node(session: Session, graph_id: UUID, node_id: UUID, request: CrawlGraphNodeUpdate) -> CrawlGraphNodeRecord:
    get_graph(session, graph_id, lock=True)
    node = _get_node(session, graph_id, node_id, lock=True)
    _require_unused(node.used_at, "node")
    node.name = _clean(request.name)
    node.description = request.description
    _flush_conflict(session, "A node with this name already exists in the graph.")
    return _node_record(node)


def update_node_position(
    session: Session,
    graph_id: UUID,
    node_id: UUID,
    request: CrawlGraphNodePositionUpdate,
) -> CrawlGraphNodeRecord:
    """Update display-only layout metadata without mutating frozen execution behavior."""

    node = _get_node(session, graph_id, node_id, lock=True)
    node.position_x = request.x
    node.position_y = request.y
    session.flush()
    return _node_record(node)


def delete_node(session: Session, graph_id: UUID, node_id: UUID) -> None:
    graph = get_graph(session, graph_id, lock=True)
    node = _get_node(session, graph_id, node_id, lock=True)
    if graph.root_node_id == node_id:
        graph.root_node_id = None
    # Explicit deletion makes connected-edge semantics identical on SQLite and Postgres.
    session.execute(
        delete(CrawlGraphEdge).where(
            CrawlGraphEdge.graph_id == graph_id,
            (CrawlGraphEdge.source_node_id == node_id) | (CrawlGraphEdge.target_node_id == node_id),
        )
    )
    session.delete(node)
    session.flush()


def create_edge(session: Session, graph_id: UUID, request: CrawlGraphEdgeCreate) -> CrawlGraphEdgeRecord:
    get_graph(session, graph_id, lock=True)
    _require_endpoints(session, graph_id, request.source_node_id, request.target_node_id)
    validate_edge_sql(request.sql)
    edge = CrawlGraphEdge(
        graph_id=graph_id,
        source_node_id=request.source_node_id,
        target_node_id=request.target_node_id,
        name=_clean(request.name),
        description=request.description,
        sql=request.sql.strip(),
        dedupe_mode=request.dedupe_mode,
    )
    session.add(edge)
    _flush_conflict(session, "An edge with this name already exists in the graph.")
    return _edge_record(edge)


def update_edge(session: Session, graph_id: UUID, edge_id: UUID, request: CrawlGraphEdgeUpdate) -> CrawlGraphEdgeRecord:
    get_graph(session, graph_id, lock=True)
    edge = _get_edge(session, graph_id, edge_id, lock=True)
    _require_unused(edge.used_at, "edge")
    _require_endpoints(session, graph_id, request.source_node_id, request.target_node_id)
    validate_edge_sql(request.sql)
    edge.source_node_id = request.source_node_id
    edge.target_node_id = request.target_node_id
    edge.name = _clean(request.name)
    edge.description = request.description
    edge.sql = request.sql.strip()
    edge.dedupe_mode = request.dedupe_mode
    _flush_conflict(session, "An edge with this name already exists in the graph.")
    return _edge_record(edge)


def delete_edge(session: Session, graph_id: UUID, edge_id: UUID) -> None:
    get_graph(session, graph_id, lock=True)
    session.delete(_get_edge(session, graph_id, edge_id, lock=True))
    session.flush()


def freeze_graph(session: Session, graph_id: UUID) -> FrozenGraphSnapshot:
    graph = get_graph(session, graph_id, lock=True)
    if graph.root_node_id is None:
        raise CrawlGraphValidationError("A crawl graph run requires a root node.")
    if not any(node.id == graph.root_node_id for node in graph.nodes):
        raise CrawlGraphValidationError("The crawl graph root node does not belong to the graph.")
    now = datetime.now(UTC)
    nodes = sorted(graph.nodes, key=lambda item: (item.created_at, str(item.id)))
    edges = sorted(graph.edges, key=lambda item: (item.created_at, str(item.id)))
    for component in [*nodes, *edges]:
        if component.used_at is None:
            component.used_at = now
    session.flush()
    return FrozenGraphSnapshot(
        graph_id=graph.id,
        root_node_id=graph.root_node_id,
        nodes=[FrozenGraphNode(id=node.id, name=node.name) for node in nodes],
        edges=[
            FrozenGraphEdge(
                id=edge.id,
                name=edge.name,
                source_node_id=edge.source_node_id,
                target_node_id=edge.target_node_id,
                sql=edge.sql,
                dedupe_mode=edge.dedupe_mode,
            )
            for edge in edges
        ],
    )


def validate_edge_sql(sql: str) -> None:
    try:
        statement = classify_select(sql)
    except CatalogueQueryError as exc:
        raise CrawlGraphValidationError(str(exc)) from exc
    placeholders = list(statement.find_all(exp.Placeholder))
    if (
        len(placeholders) != 1
        or placeholders[0].name != "crawl_id"
        or sql.count("$crawl_id") != 1
    ):
        raise CrawlGraphValidationError("Edge SQL must bind $crawl_id exactly once.")
    limit = statement.args.get("limit")
    expression = limit.args.get("expression") if isinstance(limit, exp.Limit) else None
    if not isinstance(expression, exp.Literal) or expression.is_string:
        raise CrawlGraphValidationError("Edge SQL must have an outer literal LIMIT.")
    try:
        limit_value = int(expression.this)
    except ValueError as exc:
        raise CrawlGraphValidationError("Edge SQL LIMIT must be an integer.") from exc
    if limit_value < 1 or limit_value > 100_000:
        raise CrawlGraphValidationError("Edge SQL LIMIT must be between 1 and 100000.")
    if "url" not in {selection.alias_or_name.lower() for selection in statement.selects}:
        raise CrawlGraphValidationError("Edge SQL must project a column named url.")
    if not any(table.db.lower() == "nav" for table in statement.find_all(exp.Table)):
        raise CrawlGraphValidationError("Edge SQL must read at least one nav.* table.")


def _get_node(session: Session, graph_id: UUID, node_id: UUID, *, lock: bool = False) -> CrawlGraphNode:
    statement = select(CrawlGraphNode).where(CrawlGraphNode.id == node_id, CrawlGraphNode.graph_id == graph_id)
    if lock:
        statement = statement.with_for_update()
    node = session.scalar(statement)
    if node is None:
        raise CrawlGraphNotFoundError(f"Crawl graph node {node_id} was not found.")
    return node


def _get_edge(session: Session, graph_id: UUID, edge_id: UUID, *, lock: bool = False) -> CrawlGraphEdge:
    statement = select(CrawlGraphEdge).where(CrawlGraphEdge.id == edge_id, CrawlGraphEdge.graph_id == graph_id)
    if lock:
        statement = statement.with_for_update()
    edge = session.scalar(statement)
    if edge is None:
        raise CrawlGraphNotFoundError(f"Crawl graph edge {edge_id} was not found.")
    return edge


def _require_endpoints(session: Session, graph_id: UUID, source_id: UUID, target_id: UUID) -> None:
    found = set(session.scalars(select(CrawlGraphNode.id).where(CrawlGraphNode.graph_id == graph_id, CrawlGraphNode.id.in_({source_id, target_id}))))
    if found != {source_id, target_id}:
        raise CrawlGraphValidationError("Edge source and target nodes must belong to this graph.")


def _require_unused(used_at: datetime | None, kind: str) -> None:
    if used_at is not None:
        raise CrawlGraphConflictError(f"A used crawl graph {kind} cannot be edited.")


def _flush_conflict(session: Session, message: str) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise CrawlGraphConflictError(message) from exc
