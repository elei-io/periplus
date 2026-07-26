"""Postgres-backed crawl-graph definitions and frozen execution snapshots."""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload


from .models import CrawlGraph, CrawlGraphEdge, CrawlGraphNode
from .schemas import (
    CrawlGraphCreate,
    CrawlGraphDetail,
    CrawlGraphEdgeRecord,
    CrawlGraphNodeCreate,
    CrawlGraphNodeRecord,
    CrawlGraphNodePositionUpdate,
    CrawlGraphNodeUpdate,
    CrawlGraphRecord,
    CrawlGraphUpdate,
    EdgeDedupeMode,
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
    return CrawlGraphRecord(
        id=graph.id,
        slug=graph.slug,
        description=graph.description,
        root_node_id=graph.root_node_id,
        system_owned=False,
        created_at=graph.created_at,
    )


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
    graph = CrawlGraph(slug=request.slug, description=request.description)
    session.add(graph)
    _flush_conflict(session, "A crawl graph with this slug already exists.")
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
    _require_user_owned(graph)
    if request.root_node_id is not None:
        _get_node(session, graph_id, request.root_node_id)
    graph.slug = request.slug
    graph.description = request.description
    graph.root_node_id = request.root_node_id
    _flush_conflict(session, "A crawl graph with this slug already exists.")
    return detail(graph)


def delete_graph(session: Session, graph_id: UUID) -> None:
    graph = get_graph(session, graph_id, lock=True)
    _require_user_owned(graph)
    session.delete(graph)
    session.flush()


def create_node(session: Session, graph_id: UUID, request: CrawlGraphNodeCreate) -> CrawlGraphNodeRecord:
    graph = get_graph(session, graph_id, lock=True)
    _require_user_owned(graph)
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
    _require_user_owned(get_graph(session, graph_id, lock=True))
    node = _get_node(session, graph_id, node_id, lock=True)
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

    _require_user_owned(get_graph(session, graph_id, lock=True))
    node = _get_node(session, graph_id, node_id, lock=True)
    node.position_x = request.x
    node.position_y = request.y
    session.flush()
    return _node_record(node)


def delete_node(session: Session, graph_id: UUID, node_id: UUID) -> None:
    graph = get_graph(session, graph_id, lock=True)
    _require_user_owned(graph)
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


def freeze_graph(session: Session, graph_id: UUID) -> FrozenGraphSnapshot:
    graph = get_graph(session, graph_id, lock=True)
    if graph.root_node_id is None:
        raise CrawlGraphValidationError("A crawl graph run requires a root node.")
    if not any(node.id == graph.root_node_id for node in graph.nodes):
        raise CrawlGraphValidationError("The crawl graph root node does not belong to the graph.")
    nodes = sorted(graph.nodes, key=lambda item: (item.created_at, str(item.id)))
    edges = sorted(graph.edges, key=lambda item: (item.created_at, str(item.id)))
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


def _get_node(session: Session, graph_id: UUID, node_id: UUID, *, lock: bool = False) -> CrawlGraphNode:
    statement = select(CrawlGraphNode).where(CrawlGraphNode.id == node_id, CrawlGraphNode.graph_id == graph_id)
    if lock:
        statement = statement.with_for_update()
    node = session.scalar(statement)
    if node is None:
        raise CrawlGraphNotFoundError(f"Crawl graph node {node_id} was not found.")
    return node


def _require_user_owned(graph: CrawlGraph) -> None:
    del graph


def _flush_conflict(session: Session, message: str) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise CrawlGraphConflictError(message) from exc
