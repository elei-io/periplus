"""Postgres-backed crawl-graph definitions and frozen execution snapshots."""

import re
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlglot import exp, parse
from sqlglot.errors import ParseError


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
    del request
    sequence = _next_plan_sequence(session)
    for number in range(sequence, sequence + 1_000):
        graph = CrawlGraph(slug=f"plan-{number:03d}", description=None)
        try:
            with session.begin_nested():
                session.add(graph)
                session.flush()
        except IntegrityError:
            continue
        return detail(graph)
    raise CrawlGraphConflictError(
        "Could not allocate a unique crawl plan slug."
    )


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
        raise CrawlGraphNotFoundError(f"Crawl plan {graph_id} was not found.")
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


def create_edge(
    session: Session,
    graph_id: UUID,
    request: CrawlGraphEdgeCreate,
) -> CrawlGraphEdgeRecord:
    _require_user_owned(get_graph(session, graph_id, lock=True))
    _require_endpoints(
        session, graph_id, request.source_node_id, request.target_node_id
    )
    sql = validate_edge_sql(request.sql)
    edge = CrawlGraphEdge(
        graph_id=graph_id,
        source_node_id=request.source_node_id,
        target_node_id=request.target_node_id,
        name=_clean(request.name),
        description=request.description,
        sql=sql,
    )
    session.add(edge)
    _flush_conflict(
        session, "An edge with this name already exists in the plan."
    )
    return _edge_record(edge)


def update_edge(
    session: Session,
    graph_id: UUID,
    edge_id: UUID,
    request: CrawlGraphEdgeUpdate,
) -> CrawlGraphEdgeRecord:
    _require_user_owned(get_graph(session, graph_id, lock=True))
    edge = _get_edge(session, graph_id, edge_id, lock=True)
    _require_endpoints(
        session, graph_id, request.source_node_id, request.target_node_id
    )
    edge.source_node_id = request.source_node_id
    edge.target_node_id = request.target_node_id
    edge.name = _clean(request.name)
    edge.description = request.description
    edge.sql = validate_edge_sql(request.sql)
    _flush_conflict(
        session, "An edge with this name already exists in the plan."
    )
    return _edge_record(edge)


def delete_edge(
    session: Session, graph_id: UUID, edge_id: UUID
) -> None:
    _require_user_owned(get_graph(session, graph_id, lock=True))
    session.delete(_get_edge(session, graph_id, edge_id, lock=True))
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
            )
            for edge in edges
        ],
    )


def validate_edge_sql(sql: str) -> str:
    """Validate one page-local URL selection query.

    Plan edges can read only the ephemeral ``nav.links`` relation generated
    from the document that just completed acquisition. Historical catalogue
    relations are deliberately outside the plan contract.
    """

    cleaned = sql.strip()
    try:
        statements = parse(cleaned, dialect="duckdb")
    except ParseError as exc:
        raise CrawlGraphValidationError(
            f"Invalid edge SQL: {exc}"
        ) from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise CrawlGraphValidationError(
            "An edge must contain exactly one SELECT query."
        )
    statement = statements[0]
    cte_names = {
        cte.alias_or_name.lower()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }
    reads_nav_links = False
    for source in statement.find_all(exp.Table):
        if not source.name:
            raise CrawlGraphValidationError(
                "Plan edges cannot call table functions."
            )
        if (
            not source.catalog
            and not source.db
            and source.name.lower() in cte_names
        ):
            continue
        if (
            not source.catalog
            and source.db.lower() == "nav"
            and source.name.lower() == "links"
        ):
            reads_nav_links = True
            continue
        raise CrawlGraphValidationError(
            "Plan edges may read only the current nav.links relation."
        )
    if not reads_nav_links:
        raise CrawlGraphValidationError(
            "Plan edges must read from the current nav.links relation."
        )
    if next(statement.find_all(exp.Parameter), None) is not None:
        raise CrawlGraphValidationError(
            "Plan edge SQL cannot contain runtime parameters."
        )
    for function in statement.find_all(exp.Anonymous):
        if function.name.lower() == "getenv":
            raise CrawlGraphValidationError(
                "Plan edge SQL cannot read process environment variables."
            )
    if "url" not in {name.lower() for name in statement.named_selects}:
        raise CrawlGraphValidationError(
            "Plan edge SQL must return a column named url."
        )
    return statement.sql(dialect="duckdb")


def _get_node(session: Session, graph_id: UUID, node_id: UUID, *, lock: bool = False) -> CrawlGraphNode:
    statement = select(CrawlGraphNode).where(CrawlGraphNode.id == node_id, CrawlGraphNode.graph_id == graph_id)
    if lock:
        statement = statement.with_for_update()
    node = session.scalar(statement)
    if node is None:
        raise CrawlGraphNotFoundError(f"Crawl plan node {node_id} was not found.")
    return node


def _get_edge(
    session: Session,
    graph_id: UUID,
    edge_id: UUID,
    *,
    lock: bool = False,
) -> CrawlGraphEdge:
    statement = select(CrawlGraphEdge).where(
        CrawlGraphEdge.id == edge_id,
        CrawlGraphEdge.graph_id == graph_id,
    )
    if lock:
        statement = statement.with_for_update()
    edge = session.scalar(statement)
    if edge is None:
        raise CrawlGraphNotFoundError(
            f"Crawl plan edge {edge_id} was not found."
        )
    return edge


def _require_endpoints(
    session: Session,
    graph_id: UUID,
    source_id: UUID,
    target_id: UUID,
) -> None:
    found = set(
        session.scalars(
            select(CrawlGraphNode.id).where(
                CrawlGraphNode.graph_id == graph_id,
                CrawlGraphNode.id.in_({source_id, target_id}),
            )
        )
    )
    if found != {source_id, target_id}:
        raise CrawlGraphValidationError(
            "Edge source and target nodes must belong to this plan."
        )


def _require_user_owned(graph: CrawlGraph) -> None:
    del graph


def _next_plan_sequence(session: Session) -> int:
    highest = 0
    for slug in session.scalars(
        select(CrawlGraph.slug).where(CrawlGraph.slug.like("plan-%"))
    ):
        match = re.fullmatch(r"plan-(\d+)", slug)
        if match is not None:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _flush_conflict(session: Session, message: str) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise CrawlGraphConflictError(message) from exc
