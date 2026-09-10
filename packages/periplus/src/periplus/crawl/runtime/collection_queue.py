"""Aggregate only this collection's queued interests; eligibility is not a permit."""
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import aliased

from periplus.crawl.control.domain_policies.models import DomainPolicy
from periplus.crawl.runtime.frontier_models import AcquisitionRecord, InterestRecord
from periplus.crawl.runtime.frontier_store import _current_domain_column


class QueueConstraint(BaseModel):
    reason: str
    pages: int


class CollectionQueue(BaseModel):
    runnable_pages: int = 0
    deferred_pages: int = 0
    unknown_pages: int = 0
    oldest_admitted_at: datetime | None = None
    oldest_wait_seconds: float | None = None
    constraints: tuple[QueueConstraint, ...] = ()
    basis: Literal['stored_eligibility_permits_rechecked_at_start'] = 'stored_eligibility_permits_rechecked_at_start'


def _aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def collection_queue(session, record, control, *, workers, now):
    pending = or_(InterestRecord.status == 'queued',
                  and_(InterestRecord.status == 'awaiting_result', AcquisitionRecord.status == 'retry'))
    policy_id = _current_domain_column(DomainPolicy.id)
    policy_version = _current_domain_column(DomainPolicy.version)
    active = aliased(AcquisitionRecord)
    active_count = select(func.count()).select_from(active).where(
        active.domain == AcquisitionRecord.domain, active.status == 'dispatched',
    ).correlate(AcquisitionRecord).scalar_subquery()
    domain_floor = and_(AcquisitionRecord.domain_policy_id == policy_id,
                       AcquisitionRecord.domain_policy_version == policy_version,
                       AcquisitionRecord.domain_eligible_at > now)
    global_reason = ("crawler_paused" if control.paused else None)
    deadline = record.deadline_at
    if record.status == 'paused':
        global_reason = 'collection_paused'
    elif deadline and deadline <= now:
        global_reason = 'collection_duration_limit'
    if global_reason:
        reason = literal(global_reason)
    else:
        ready = (workers is not None and workers.state == 'observed' and workers.ready_workers
                 and 0 <= (now - workers.as_of).total_seconds() <= 3)
        final = ('exclusions_require_candidate_evaluation' if control.exclusions else
                 'runnable' if ready else 'worker_readiness_not_observed')
        reason = case(
            (policy_id.is_(None), 'domain_policy_unavailable'),
            (_current_domain_column(DomainPolicy.paused).is_(True), 'domain_paused'),
            (AcquisitionRecord.eligible_at > now, func.coalesce(AcquisitionRecord.defer_reason, 'retry_backoff')),
            (domain_floor, 'domain_pacing'),
            (active_count >= _current_domain_column(DomainPolicy.maximum_concurrency), 'domain_capacity'),
            (literal(control.active_count >= control.dispatch_limit), 'dispatch_capacity'),
            (literal(control.next_dispatch_at is not None and _aware(control.next_dispatch_at) > now), 'global_pacing'),
            else_=final,
        )
    # One grouped query, returning a bounded reason vocabulary rather than URLs,
    # caller IDs, policy payloads, or a page of acquisitions to count in Python.
    candidates = (select(reason.label('reason'), InterestRecord.created_at.label('admitted_at'))
        .select_from(InterestRecord).join(AcquisitionRecord, AcquisitionRecord.id == InterestRecord.acquisition_id)
        .where(InterestRecord.collection_id == record.id, pending).subquery())
    rows = session.execute(select(candidates.c.reason, func.count(), func.min(candidates.c.admitted_at))
                           .group_by(candidates.c.reason)).all()
    result = CollectionQueue()
    constraints = []
    oldest = None
    for constraint, pages, admitted in rows:
        admitted = _aware(admitted)
        oldest = min(oldest, admitted) if oldest else admitted
        if constraint == 'runnable':
            result.runnable_pages += pages
        elif constraint in {'domain_policy_unavailable', 'worker_readiness_not_observed',
                            'exclusions_require_candidate_evaluation'}:
            result.unknown_pages += pages
        else:
            result.deferred_pages += pages
        if constraint != 'runnable':
            constraints.append(QueueConstraint(reason=constraint, pages=pages))
    result.oldest_admitted_at = oldest
    result.oldest_wait_seconds = max(0, (now - oldest).total_seconds()) if oldest else None
    result.constraints = tuple(sorted(constraints, key=lambda item: item.reason))
    return result
