"""Finite collection intent over the shared frontier; handlers never run traversal."""
import asyncio
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from periplus.crawl.control.collections.arrivals import CollectionArrivalsPage
from periplus.crawl.control.collections.history import CollectionHistoryPage, HistoricalCollection, HistoryUnavailable
from periplus.crawl.control.collections.schemas import CollectionExecutionSpec, CollectionSpec
from periplus.crawl.runtime.frontier_store import AdmissionDeferred, CollectionUnavailable
from periplus.crawl.runtime.frontier_items import CollectionItemsPage, collection_items, enrich_readiness
from periplus.crawl.runtime.frontier_views import CollectionView, collection_views, enrich_collection_readiness
from periplus.crawl.runtime.selection_sql import validate_follow_sql

router = APIRouter(prefix="/collections", tags=["Collections"])


class CreateCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID = Field(default_factory=uuid4)
    specification: CollectionSpec
    priority: int = Field(default=0, ge=-10, le=10)


class CollectionPage(BaseModel):
    source: Literal["current"] = "current"
    items: list[CollectionView]
    limit: int
    offset: int


class CollectionAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["pause", "resume", "cancel"]


async def _views(request: Request, **kwargs):
    workers = await request.app.state.crawler_presence.read()
    return await asyncio.to_thread(collection_views, request.app.state.frontier_sessions,
                                   workers=workers, **kwargs)


@router.post("", response_model=CollectionView | HistoricalCollection, status_code=201)
async def create(payload: CreateCollection, request: Request):
    spec = payload.specification
    if spec.origin is not None:
        raise HTTPException(422, "Request origin is assigned by the scheduler.")
    if request.state.api_role != "admin":
        if payload.priority != 0:
            raise HTTPException(403, "Priority requires administrative access.")
        spec = spec.model_copy(update={"request_class": "public"})
    if not spec.seed_urls and not spec.seed_sql and not spec.seed_description:
        raise HTTPException(422, "Provide starting URLs, a source description, or seed SQL.")
    try:
        validate_follow_sql(spec.follow_sql)
        existing = await asyncio.to_thread(request.app.state.frontier.get_collection, payload.id)
        # Server-generated UUIDs are new identities. A caller-supplied identity
        # must also be checked in immutable history before it can be recreated.
        if existing is None and "id" in payload.model_fields_set:
            try:
                retired = await request.app.state.collection_history.is_retired(payload.id)
            except HistoryUnavailable as exc:
                raise HTTPException(503, "Retention status is unavailable; retry later.") from exc
            if retired:
                raise HTTPException(410, "This request has expired and been retired. Submit a new request identity.")
            historical = await _history(request, payload.id)
            if historical is not None:
                if historical.specification.model_dump(exclude={"deadline_at"}) != spec.model_dump():
                    raise HTTPException(409, "Collection identity already has different immutable intent.")
                return historical
        if existing is None:
            if request.state.api_role == "public":
                from periplus.operations.access.service import AccessStore
                await asyncio.to_thread(AccessStore(request.app.state.frontier_sessions).admit, "crawl", specification=spec)
            await asyncio.to_thread(request.app.state.frontier.create_collection, payload.id, spec, priority=payload.priority)
        elif CollectionExecutionSpec.model_validate(existing.spec).model_dump(mode="json", exclude={"deadline_at"}) != spec.model_dump(mode="json"):
            raise ValueError("collection identity reused with different intent")
    except AdmissionDeferred as exc:
        raise HTTPException(429, "Collection admission is at capacity; retry later.", headers={"Retry-After": "15"}) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    views = await _views(request, identity=payload.id)
    if views:
        return (await enrich_collection_readiness(views, request.app.state.collection_history))[0]
    historical = await _history(request, payload.id)
    if historical is None:
        raise HTTPException(503, "Collection status is temporarily unavailable.")
    return historical


@router.get("", response_model=CollectionPage)
async def list_collections(request: Request, status: Literal["active", "paused", "settled"] | None = None,
                     request_class: Literal["public", "system", "admin"] | None = None,
                     limit: Annotated[int, Query(ge=1, le=100)] = 20,
                     offset: Annotated[int, Query(ge=0, le=10000)] = 0):
    views = await _views(request, status=status, request_class=request_class, limit=limit, offset=offset)
    views = await enrich_collection_readiness(views, request.app.state.collection_history)
    return CollectionPage(items=views, limit=limit, offset=offset)


@router.get("/history", response_model=CollectionHistoryPage)
async def history_page(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                       cursor: Annotated[str | None, Query(max_length=512)] = None):
    try:
        page = await request.app.state.collection_history.list(limit=limit, cursor=cursor)
        return page
    except ValueError as exc:
        raise HTTPException(422, "Invalid history cursor or page limit.") from exc
    except HistoryUnavailable as exc:
        raise HTTPException(503, "Collection history is unavailable; retry later.", headers={"Retry-After": "5"}) from exc


async def _history(request: Request, identity: UUID):
    try:
        historical = await request.app.state.collection_history.get(identity)
        if historical is not None:
            return (await enrich_collection_readiness([historical], request.app.state.collection_history))[0]
        return None
    except HistoryUnavailable as exc:
        raise HTTPException(503, "Collection history is unavailable; retry later.", headers={"Retry-After": "5"}) from exc


@router.get("/{identity}", response_model=CollectionView | HistoricalCollection)
async def detail(identity: UUID, request: Request):
    views = await _views(request, identity=identity)
    if views:
        return (await enrich_collection_readiness(views, request.app.state.collection_history))[0]
    historical = await _history(request, identity)
    if historical is None:
        raise HTTPException(404, "Collection not found.")
    return historical


@router.post("/{identity}/actions", response_model=CollectionView)
async def act(identity: UUID, payload: CollectionAction, request: Request):
    if request.state.api_role != "admin":
        raise HTTPException(403, "Collection control requires administrative access.")
    if not await _views(request, identity=identity):
        raise HTTPException(404, "Collection not found.")
    try:
        if payload.action == "cancel":
            await asyncio.to_thread(request.app.state.frontier.stop_collection, identity)
        else:
            await asyncio.to_thread(request.app.state.frontier.set_collection_paused, identity, payload.action == "pause")
    except CollectionUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    return (await _views(request, identity=identity))[0]


class CollectionPriority(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: int = Field(ge=-10, le=10)


@router.put("/{identity}/priority", response_model=CollectionView)
async def prioritize(identity: UUID, payload: CollectionPriority, request: Request):
    if request.state.api_role != "admin":
        raise HTTPException(403, "Collection control requires administrative access.")
    try:
        await asyncio.to_thread(request.app.state.frontier.set_collection_priority, identity, payload.priority)
    except KeyError as exc:
        raise HTTPException(404, "Collection not found.") from exc
    except CollectionUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    return (await _views(request, identity=identity))[0]


@router.get("/{identity}/items", response_model=CollectionItemsPage)
async def items(identity: UUID, request: Request,
          limit: Annotated[int, Query(ge=1, le=100)] = 20, after: UUID | None = None):
    workers = await request.app.state.crawler_presence.read()
    page = await asyncio.to_thread(collection_items, request.app.state.frontier_sessions, identity,
        limit=limit, after=after, workers=workers)
    if page is None:
        raise HTTPException(404, "Current collection not found; use its durable history for historical arrivals.")
    enriched = await enrich_readiness([item.acquisition for item in page.items],
        request.app.state.collection_history)
    return page.model_copy(update={"items": [item.model_copy(update={"acquisition": acquisition})
        for item, acquisition in zip(page.items, enriched, strict=True)]})


@router.get("/{identity}/arrivals", response_model=CollectionArrivalsPage)
async def arrivals(identity: UUID, request: Request,
                   limit: Annotated[int, Query(ge=1, le=100)] = 20,
                   cursor: Annotated[str | None, Query(max_length=512)] = None):
    try:
        page = await request.app.state.collection_history.arrivals(identity,
            limit=limit, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(422, "Invalid arrival cursor or page limit.") from exc
    except HistoryUnavailable as exc:
        raise HTTPException(503, "Collection arrivals are unavailable; retry later.",
                            headers={"Retry-After": "5"}) from exc
    if page is not None:
        return page
    # Newly accepted intent may precede its immutable definition commit. Empty
    # history is an explicit ingestion milestone, not a missing current request.
    if await _views(request, identity=identity):
        return CollectionArrivalsPage(collection_id=identity, definition_committed=False,
            items=[], next_cursor=None, as_of=datetime.now(UTC))
    raise HTTPException(404, "Collection not found.")
