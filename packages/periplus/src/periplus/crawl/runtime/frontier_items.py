"""Bounded current frontier drilldowns. Immutable historical arrivals remain in the lake."""
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import aliased, load_only

from periplus.crawl.control.domain_policies.models import DomainPolicy
from periplus.crawl.runtime.start_estimates import StartEstimate, estimate_start
from periplus.crawl.runtime.frontier_store import FrontierStore, _current_domain_column
from periplus.crawl.control.collections.models import CollectionRecord
from periplus.crawl.control.collections.schemas import SelectionContext
from periplus.crawl.runtime.frontier_models import AcquisitionRecord, FrontierControlRecord, InterestRecord


_ACQUISITION_COLUMNS = (AcquisitionRecord.id, AcquisitionRecord.visibility, AcquisitionRecord.url, AcquisitionRecord.domain,
    AcquisitionRecord.status, AcquisitionRecord.created_at, AcquisitionRecord.completed_at,
    AcquisitionRecord.attempt_count, AcquisitionRecord.evidence_snapshot, AcquisitionRecord.eligible_at,
    AcquisitionRecord.attempt_started_at, AcquisitionRecord.domain_eligible_at,
    AcquisitionRecord.domain_policy_id, AcquisitionRecord.domain_policy_version, AcquisitionRecord.background_reason, AcquisitionRecord.defer_reason, AcquisitionRecord.terminal_reason)
_OBSERVATION_ID = AcquisitionRecord.outcome["visit"]["visit_id"].as_string()


class Caller(BaseModel):
    collection_id: UUID
    interest_id: UUID
    status: str
    mode: str


class AcquisitionView(BaseModel):
    id: UUID
    url: str
    domain: str
    status: str
    created_at: datetime
    completed_at: datetime | None
    attempt_count: int
    observation_id: UUID | None
    evidence_committed: bool
    terminal_reason: str | None = None
    query_ready: bool | None = None
    query_readiness_reason: str = "materialization_commit_not_verified"
    eligibility_not_before: datetime | None
    waiting_reason: str | None
    next_start_estimate: StartEstimate | None = None
    estimate_unavailable_reason: str | None
    callers: list[Caller]
    more_callers: bool
    background: bool
    background_parent_observation_id: UUID | None
    background_rule_id: str | None
    as_of: datetime


class CollectionItem(BaseModel):
    interest_id: UUID
    status: str
    mode: str
    budget_state: str
    context: SelectionContext
    admitted_at: datetime
    acquisition: AcquisitionView


class CollectionItemsPage(BaseModel):
    source: Literal["current"] = "current"
    collection_id: UUID
    items: list[CollectionItem]
    next_after: UUID | None
    as_of: datetime
    ordering: Literal["interest_identity_not_dispatch_order"] = "interest_identity_not_dispatch_order"


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _callers(session, identities: list[UUID], public_only: bool):
    # Visibility is applied before windowing, so private callers affect neither
    # preview membership nor the overflow flag. Never load collection intent here.
    statement = select(InterestRecord.acquisition_id, InterestRecord.collection_id,
        InterestRecord.id.label("interest_id"), InterestRecord.status, InterestRecord.mode,
        func.row_number().over(partition_by=InterestRecord.acquisition_id,
                               order_by=InterestRecord.id).label("position")).join(
            CollectionRecord, CollectionRecord.id == InterestRecord.collection_id).where(
                InterestRecord.acquisition_id.in_(identities), CollectionRecord.retiring.is_(False))
    if public_only:
        statement = statement.where(CollectionRecord.spec["visibility"].as_string() == "public")
    ranked = statement.subquery()
    rows = session.execute(select(ranked).where(ranked.c.position <= 11).order_by(
        ranked.c.acquisition_id, ranked.c.position)).mappings()
    result: dict[UUID, list[Caller]] = {identity: [] for identity in identities}
    for row in rows:
        result[row["acquisition_id"]].append(Caller(collection_id=row["collection_id"],
            interest_id=row["interest_id"], status=row["status"], mode=row["mode"]))
    return result


def _waiting_constraints(session, identities):
    """Read current domain constraints for the bounded visible page, without policy-table expansion."""
    active = aliased(AcquisitionRecord)
    active_count = select(func.count()).select_from(active).where(
        active.domain == AcquisitionRecord.domain, active.status == "dispatched",
    ).correlate(AcquisitionRecord).scalar_subquery()
    rows = session.execute(select(AcquisitionRecord.id,
        _current_domain_column(DomainPolicy.id), _current_domain_column(DomainPolicy.version),
        _current_domain_column(DomainPolicy.paused),
        _current_domain_column(DomainPolicy.maximum_concurrency), active_count,
    ).where(AcquisitionRecord.id.in_(identities)))
    return {row[0]: tuple(row[1:]) for row in rows}


def _view(record: AcquisitionRecord, callers: list[Caller], control: FrontierControlRecord,
          now: datetime, observation_id: str | None, constraint, session, workers) -> AcquisitionView:
    pending = record.status in {"queued", "retry"}
    waiting = None
    eligible = _aware(record.eligible_at) if pending else None
    if pending:
        policy_id, version, paused, maximum, active = constraint
        domain_floor = (_aware(record.domain_eligible_at) if record.domain_eligible_at is not None
            and record.domain_policy_id == policy_id and record.domain_policy_version == version else None)
        if domain_floor is not None:
            eligible = max(eligible, domain_floor)
        if control.next_dispatch_at is not None:
            eligible = max(eligible, _aware(control.next_dispatch_at))
        waiting = FrontierStore._attempt_waiting_reason(control)
        if waiting is None:
            if policy_id is None:
                waiting = "domain_policy_unavailable"
            elif paused:
                waiting = "domain_paused"
            elif _aware(record.eligible_at) > now:
                waiting = record.defer_reason or "retry_backoff"
            elif domain_floor is not None and domain_floor > now:
                waiting = "domain_pacing"
            elif active >= maximum:
                waiting = "domain_capacity"
            elif control.active_count >= control.dispatch_limit:
                waiting = "dispatch_capacity"
            elif control.next_dispatch_at is not None and _aware(control.next_dispatch_at) > now:
                waiting = "global_pacing"
            else:
                waiting = "awaiting_scheduler_evaluation"
    elif record.status == "dispatched":
        waiting = "acquiring" if record.attempt_started_at else "awaiting_capture_start"
    estimate, estimate_reason = estimate_start(session, record, control, waiting=waiting, eligible_at=eligible,
        constraint=constraint, workers=workers, now=now)
    background = record.background_reason or {}
    # Return only the defined public causal fields, never arbitrary provider data,
    # raw errors, policy snapshots, credentials, or selection SQL.
    return AcquisitionView(id=record.id, url=record.url, domain=record.domain, status=record.status,
        created_at=record.created_at, completed_at=record.completed_at, attempt_count=record.attempt_count,
        observation_id=observation_id, terminal_reason=record.terminal_reason,
        evidence_committed=record.evidence_snapshot is not None,
        eligibility_not_before=eligible, waiting_reason=waiting,
        next_start_estimate=estimate,
        estimate_unavailable_reason="already_started" if record.attempt_started_at else
            "acquisition_terminal" if record.status in {"succeeded", "failed", "cancelled"} else
            estimate_reason,
        callers=callers[:10], more_callers=len(callers) > 10,
        background=bool(background), background_parent_observation_id=background.get("parent_observation_id"),
        background_rule_id=background.get("rule_id"), as_of=now)


def collection_items(sessions, identity: UUID, *, public_only: bool = True,
                     limit: int = 20, after: UUID | None = None, workers=None) -> CollectionItemsPage | None:
    if not 1 <= limit <= 100:
        raise ValueError("frontier page limit must be between 1 and 100")
    with sessions() as session:
        control = session.scalar(select(FrontierControlRecord).where(
            FrontierControlRecord.id == 1).with_for_update(read=True))
        collection_query = select(CollectionRecord.id).where(
            CollectionRecord.id == identity, CollectionRecord.retiring.is_(False))
        if public_only:
            collection_query = collection_query.where(CollectionRecord.spec["visibility"].as_string() == "public")
        if session.scalar(collection_query) is None:
            return None
        statement = select(InterestRecord, AcquisitionRecord, _OBSERVATION_ID).options(
            load_only(InterestRecord.id, InterestRecord.status, InterestRecord.mode, InterestRecord.budget_state,
                      InterestRecord.context, InterestRecord.created_at),
            load_only(*_ACQUISITION_COLUMNS)).join(AcquisitionRecord,
            AcquisitionRecord.id == InterestRecord.acquisition_id).where(InterestRecord.collection_id == identity)
        if public_only:
            statement = statement.where(AcquisitionRecord.visibility == "public")
        if after is not None:
            statement = statement.where(InterestRecord.id > after)
        rows = list(session.execute(statement.order_by(InterestRecord.id).limit(limit + 1)))
        selected = rows[:limit]
        callers = _callers(session, [record.id for _, record, _ in selected], public_only) if selected else {}
        constraints = _waiting_constraints(session, [record.id for _, record, _ in selected]) if selected else {}
        now = datetime.now(UTC)
        return CollectionItemsPage(collection_id=identity, as_of=now,
            next_after=selected[-1][0].id if len(rows) > limit else None,
            items=[CollectionItem(interest_id=interest.id, status=interest.status, mode=interest.mode,
                budget_state=interest.budget_state, context=SelectionContext.model_validate(interest.context),
                admitted_at=interest.created_at, acquisition=_view(record, callers[record.id], control, now, observation_id, constraints[record.id], session, workers))
                for interest, record, observation_id in selected])


def acquisition_view(sessions, identity: UUID, *, public_only: bool = True, workers=None) -> AcquisitionView | None:
    with sessions() as session:
        control = session.scalar(select(FrontierControlRecord).where(
            FrontierControlRecord.id == 1).with_for_update(read=True))
        statement = select(AcquisitionRecord, _OBSERVATION_ID).options(load_only(*_ACQUISITION_COLUMNS)).where(AcquisitionRecord.id == identity)
        if public_only:
            statement = statement.where(AcquisitionRecord.visibility == "public")
        row = session.execute(statement).first()
        if row is None:
            return None
        record, observation_id = row
        return _view(record, _callers(session, [identity], public_only)[identity], control, datetime.now(UTC), observation_id,
            _waiting_constraints(session, [identity])[identity], session, workers)


async def enrich_readiness(items: list[AcquisitionView], history, *, public_only: bool) -> list[AcquisitionView]:
    """Verify only visible page members, after the control-state transaction closes."""
    from periplus.crawl.control.collections.history import HistoryUnavailable
    identities = list(dict.fromkeys(item.observation_id for item in items if item.observation_id is not None))
    if not identities:
        return items
    try:
        proofs = await history.readiness(identities, public_only=public_only)
    except HistoryUnavailable:
        return [item.model_copy(update={"query_ready": None,
                    "query_readiness_reason": "catalogue_readiness_unavailable"})
                if item.observation_id is not None else item for item in items]
    result = []
    for item in items:
        proof = proofs.get(item.observation_id)
        if proof is None:
            result.append(item)
        else:
            result.append(item.model_copy(update={"query_ready": proof.query_ready,
                "query_readiness_reason": proof.reason,
                "evidence_committed": item.evidence_committed or proof.query_ready is not None}))
    return result
