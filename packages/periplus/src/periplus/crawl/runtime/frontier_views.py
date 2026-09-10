"""Bounded current-state reads; historical arrivals remain lake evidence."""
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import computed_field, BaseModel
from sqlalchemy import func, select

from periplus.crawl.control.collections.discovery import DiscoveryState
from periplus.crawl.control.collections.models import CollectionRecord
from periplus.crawl.control.collections.schemas import CollectionExecutionSpec, CollectionSpec
from periplus.crawl.runtime.frontier_models import AcquisitionRecord, FrontierControlRecord, FrontierOutboxRecord, InterestRecord
from periplus.crawl.runtime.admission_wait import AdmissionWait, admission_wait
from periplus.crawl.runtime.collection_queue import CollectionQueue, collection_queue, _aware


class CollectionView(BaseModel):
    source: Literal["current"] = "current"
    id: UUID
    specification: CollectionExecutionSpec
    status: Literal["active", "paused", "settled"]
    priority: int
    reserved_pages: int
    consumed_pages: int
    seeds_settled: bool
    waiting_reason: str | None
    outcome: str | None
    created_at: datetime
    completed_at: datetime | None
    queued_pages: int = 0
    queue: CollectionQueue
    last_progress_at: datetime | None = None
    acquiring_pages: int = 0
    selecting_pages: int = 0
    supplied_pages: int = 0
    failed_pages: int = 0
    shared_pages: int = 0
    reused_pages: int = 0
    ingested_pages: int = 0
    lineage_ready: bool = False
    discovery_stage: str | None = None
    search_queries: tuple[str, ...] = ()
    resolved_urls: tuple[str, ...] = ()
    admission: AdmissionWait
    query_ready: bool | None = None
    query_readiness_reason: str = "catalogue_commit_not_verified"
    query_readiness_as_of: datetime | None = None
    query_generation_id: UUID | None = None
    as_of: datetime


    @computed_field
    @property
    def expires_at(self) -> datetime | None:
        from periplus.retention.policy import expires_at
        return expires_at(self.specification.retention_seconds, self.completed_at)

    @computed_field
    @property
    def retention_expired(self) -> bool:
        expiry = self.expires_at
        return expiry is not None and expiry <= self.as_of


def collection_views(sessions, *, identity: UUID | None = None, status: str | None = None, request_class: Literal["public", "system", "admin"] | None = None, limit: int = 20, offset: int = 0, workers=None) -> list[CollectionView]:
    if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
        raise ValueError("collection page outside bounds")
    if status is not None and status not in {"active", "paused", "settled"}:
        raise ValueError("invalid collection status")
    with sessions() as session:
        control = session.scalar(select(FrontierControlRecord).where(FrontierControlRecord.id == 1).with_for_update(read=True))
        statement = select(CollectionRecord).where(CollectionRecord.retiring.is_(False))
        if identity is not None:
            statement = statement.where(CollectionRecord.id == identity)
        if status is not None:
            statement = statement.where(CollectionRecord.status == status)
        if request_class is not None:
            statement = statement.where(CollectionRecord.spec["request_class"].as_string() == request_class)
        records = list(session.scalars(statement.order_by(CollectionRecord.created_at.desc(),
                                                         CollectionRecord.id.desc()).limit(limit).offset(offset)))
        as_of = datetime.now(UTC)
        views = {record.id: CollectionView(
            id=record.id, specification=CollectionExecutionSpec.model_validate(record.spec), status=record.status,
            priority=record.priority, reserved_pages=record.reserved, consumed_pages=record.consumed,
            seeds_settled=record.seeds_settled, waiting_reason=record.waiting_reason, outcome=record.outcome,
            created_at=record.created_at, completed_at=record.completed_at, as_of=as_of,
            admission=admission_wait(session, record, now=as_of, control=control, workers=workers),
            queue=collection_queue(session, record, control, workers=workers, now=as_of),
            last_progress_at=max(_aware(value) for value in (record.last_progress_at,
                record.last_dispatch_at, record.completed_at, record.created_at) if value is not None),
        ) for record in records}
        for record in records:
            if record.spec.get("seed_description"):
                state = DiscoveryState.model_validate(record.discovery_state or {})
                view = views[record.id]
                view.search_queries, view.resolved_urls = state.queries, state.urls
                view.discovery_stage = ("complete" if state.complete else "planning" if not state.queries
                                        else "searching" if len(state.searches) < len(state.queries)
                                        else "selecting" if state.selected is None else "validating")
        if not views:
            return []
        rows = session.execute(select(InterestRecord.collection_id, InterestRecord.status,
                                      InterestRecord.mode, AcquisitionRecord.status, func.count()).join(
            AcquisitionRecord, AcquisitionRecord.id == InterestRecord.acquisition_id,
        ).where(InterestRecord.collection_id.in_(views)).group_by(
            InterestRecord.collection_id, InterestRecord.status, InterestRecord.mode, AcquisitionRecord.status))
        for collection_id, interest_status, mode, acquisition_status, count in rows:
            view = views[collection_id]
            if interest_status == "queued":
                view.queued_pages += count
            if interest_status == "awaiting_result":
                if acquisition_status == "retry":
                    view.queued_pages += count
                else:
                    view.acquiring_pages += count
            if interest_status == "selecting":
                view.selecting_pages += count
            if interest_status in {"selecting", "settled"}:
                view.supplied_pages += count if acquisition_status == "succeeded" else 0
                view.failed_pages += count if acquisition_status == "failed" else 0
            view.shared_pages += count if mode == "shared" else 0
            view.reused_pages += count if mode == "reused" else 0
        for collection_id, count in session.execute(select(
            InterestRecord.collection_id, func.count(),
        ).join(AcquisitionRecord, AcquisitionRecord.id == InterestRecord.acquisition_id).where(
            InterestRecord.collection_id.in_(views),
            AcquisitionRecord.evidence_snapshot.is_not(None),
            InterestRecord.status.in_(("selecting", "settled")),
        ).group_by(InterestRecord.collection_id)):
            views[collection_id].ingested_pages = count
        # Collection definitions, causal reasons, fulfillments and terminal outcome
        # are separate append-only commits. A base observation receipt proves none
        # of those on its own.
        uncommitted = set(session.scalars(select(FrontierOutboxRecord.collection_id).where(
            FrontierOutboxRecord.collection_id.in_(views),
            FrontierOutboxRecord.kind == "lineage",
            FrontierOutboxRecord.committed_snapshot.is_(None),
        ).distinct()))
        confirmed_outcomes = set(session.scalars(select(FrontierOutboxRecord.collection_id).where(
            FrontierOutboxRecord.collection_id.in_(views),
            FrontierOutboxRecord.kind == "lineage",
            FrontierOutboxRecord.payload["kind"].as_string() == "collection_outcome",
            FrontierOutboxRecord.committed_snapshot.is_not(None),
        )))
        for identity, view in views.items():
            view.lineage_ready = (view.status == "settled" and identity in confirmed_outcomes
                                  and identity not in uncommitted)
            if view.lineage_ready and view.ingested_pages == view.supplied_pages + view.failed_pages:
                view.query_readiness_reason = "materialization_commit_not_verified"
        return list(views.values())


async def enrich_collection_readiness(views, history):
    from periplus.crawl.control.collections.history import HistoryUnavailable
    settled = [view for view in views if view.completed_at is not None]
    if not settled:
        return views
    try:
        proofs = await history.collection_readiness([view.id for view in settled])
    except HistoryUnavailable:
        proofs = {}
    result = []
    for view in views:
        if view.completed_at is None:
            result.append(view)
            continue
        proof = proofs.get(view.id)
        result.append(view.model_copy(update={
            "query_ready": proof.query_ready if proof else None,
            "query_readiness_reason": proof.reason if proof else "catalogue_readiness_unavailable",
            "query_readiness_as_of": proof.as_of if proof else None,
            "query_generation_id": proof.generation_id if proof else None,
        }))
    return result
