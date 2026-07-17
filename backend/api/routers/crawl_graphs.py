from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from control.crawl_graphs.schemas import (
    CrawlGraphCreate,
    CrawlGraphDetail,
    CrawlGraphEdgeCreate,
    CrawlGraphEdgeRecord,
    CrawlGraphEdgeUpdate,
    CrawlGraphListResponse,
    CrawlGraphNodeCreate,
    CrawlGraphNodeRecord,
    CrawlGraphNodePositionUpdate,
    CrawlGraphNodeUpdate,
    CrawlGraphUpdate,
)
from control.crawl_graphs.service import (
    CrawlGraphConflictError,
    CrawlGraphNotFoundError,
    CrawlGraphValidationError,
    create_edge,
    create_graph,
    create_node,
    delete_edge,
    delete_graph,
    delete_node,
    detail,
    get_graph,
    list_graphs,
    update_edge,
    update_graph,
    update_node,
    update_node_position,
)
from db.session import get_session

router = APIRouter(prefix="/crawl-graphs", tags=["crawl-graphs"])


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, CrawlGraphNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, CrawlGraphConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/", response_model=CrawlGraphListResponse)
def list_(session: Annotated[Session, Depends(get_session)]) -> CrawlGraphListResponse:
    items = list_graphs(session)
    return CrawlGraphListResponse(items=items, total=len(items))


@router.post("/", response_model=CrawlGraphDetail, status_code=201)
def create(payload: CrawlGraphCreate, session: Annotated[Session, Depends(get_session)]) -> CrawlGraphDetail:
    try:
        return create_graph(session, payload)
    except (CrawlGraphConflictError, CrawlGraphValidationError) as exc:
        raise _translate(exc) from exc


@router.get("/{graph_id}", response_model=CrawlGraphDetail)
def get(graph_id: UUID, session: Annotated[Session, Depends(get_session)]) -> CrawlGraphDetail:
    try:
        return detail(get_graph(session, graph_id))
    except CrawlGraphNotFoundError as exc:
        raise _translate(exc) from exc


@router.put("/{graph_id}", response_model=CrawlGraphDetail)
def update(graph_id: UUID, payload: CrawlGraphUpdate, session: Annotated[Session, Depends(get_session)]) -> CrawlGraphDetail:
    try:
        return update_graph(session, graph_id, payload)
    except (CrawlGraphConflictError, CrawlGraphNotFoundError, CrawlGraphValidationError) as exc:
        raise _translate(exc) from exc


@router.delete("/{graph_id}", status_code=204)
def delete(graph_id: UUID, session: Annotated[Session, Depends(get_session)]) -> None:
    try:
        delete_graph(session, graph_id)
    except (CrawlGraphConflictError, CrawlGraphNotFoundError) as exc:
        raise _translate(exc) from exc


@router.post("/{graph_id}/nodes", response_model=CrawlGraphNodeRecord, status_code=201)
def create_node_(graph_id: UUID, payload: CrawlGraphNodeCreate, session: Annotated[Session, Depends(get_session)]) -> CrawlGraphNodeRecord:
    try:
        return create_node(session, graph_id, payload)
    except (CrawlGraphNotFoundError, CrawlGraphConflictError, CrawlGraphValidationError) as exc:
        raise _translate(exc) from exc


@router.put("/{graph_id}/nodes/{node_id}", response_model=CrawlGraphNodeRecord)
def update_node_(graph_id: UUID, node_id: UUID, payload: CrawlGraphNodeUpdate, session: Annotated[Session, Depends(get_session)]) -> CrawlGraphNodeRecord:
    try:
        return update_node(session, graph_id, node_id, payload)
    except (CrawlGraphNotFoundError, CrawlGraphConflictError, CrawlGraphValidationError) as exc:
        raise _translate(exc) from exc


@router.put("/{graph_id}/nodes/{node_id}/position", response_model=CrawlGraphNodeRecord)
def update_node_position_(graph_id: UUID, node_id: UUID, payload: CrawlGraphNodePositionUpdate, session: Annotated[Session, Depends(get_session)]) -> CrawlGraphNodeRecord:
    try:
        return update_node_position(session, graph_id, node_id, payload)
    except (CrawlGraphConflictError, CrawlGraphNotFoundError) as exc:
        raise _translate(exc) from exc


@router.delete("/{graph_id}/nodes/{node_id}", status_code=204)
def delete_node_(graph_id: UUID, node_id: UUID, session: Annotated[Session, Depends(get_session)]) -> None:
    try:
        delete_node(session, graph_id, node_id)
    except (CrawlGraphConflictError, CrawlGraphNotFoundError) as exc:
        raise _translate(exc) from exc


@router.post("/{graph_id}/edges", response_model=CrawlGraphEdgeRecord, status_code=201)
def create_edge_(graph_id: UUID, payload: CrawlGraphEdgeCreate, session: Annotated[Session, Depends(get_session)]) -> CrawlGraphEdgeRecord:
    try:
        return create_edge(session, graph_id, payload)
    except (CrawlGraphNotFoundError, CrawlGraphConflictError, CrawlGraphValidationError) as exc:
        raise _translate(exc) from exc


@router.put("/{graph_id}/edges/{edge_id}", response_model=CrawlGraphEdgeRecord)
def update_edge_(graph_id: UUID, edge_id: UUID, payload: CrawlGraphEdgeUpdate, session: Annotated[Session, Depends(get_session)]) -> CrawlGraphEdgeRecord:
    try:
        return update_edge(session, graph_id, edge_id, payload)
    except (CrawlGraphNotFoundError, CrawlGraphConflictError, CrawlGraphValidationError) as exc:
        raise _translate(exc) from exc


@router.delete("/{graph_id}/edges/{edge_id}", status_code=204)
def delete_edge_(graph_id: UUID, edge_id: UUID, session: Annotated[Session, Depends(get_session)]) -> None:
    try:
        delete_edge(session, graph_id, edge_id)
    except (CrawlGraphConflictError, CrawlGraphNotFoundError) as exc:
        raise _translate(exc) from exc
