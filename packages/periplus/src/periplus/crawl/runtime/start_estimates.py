"""Conditional observed wait ranges, never scheduling reservations or guarantees."""
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from periplus.crawl.control.collections.models import CollectionRecord
from periplus.crawl.runtime.frontier_models import AcquisitionRecord, InterestRecord


class StartEstimate(BaseModel):
    earliest_at: datetime
    latest_at: datetime
    calculated_at: datetime
    expires_at: datetime
    sample_size: int = Field(ge=3, le=20)
    sample_window_seconds: Literal[600] = 600
    basis: Literal['recent_comparable_observed_wait_range'] = 'recent_comparable_observed_wait_range'
    uncertainty: Literal['conditional_not_a_guarantee'] = 'conditional_not_a_guarantee'
    assumptions: Literal['unchanged_policies_worker_readiness_and_competing_work'] = 'unchanged_policies_worker_readiness_and_competing_work'


def observed_range(waits, *, admitted_at, eligible_at, now):
    """Condition on already elapsed waiting; do not project an overdue sample into the past."""
    elapsed = max(0, (now - admitted_at).total_seconds())
    waits = sorted(value for value in waits if elapsed <= value <= 600)
    if len(waits) < 3:
        return None
    earliest = max(now + timedelta(seconds=waits[0] - elapsed), eligible_at)
    latest = now + timedelta(seconds=waits[-1] - elapsed)
    if latest <= earliest:
        return None
    return StartEstimate(earliest_at=earliest, latest_at=latest, calculated_at=now,
        expires_at=now + timedelta(seconds=5), sample_size=len(waits))


def estimate_start(session, record, control, *, waiting, eligible_at, constraint, workers, now):
    if record.status != 'queued' or waiting != 'awaiting_scheduler_evaluation':
        return None, waiting or 'acquisition_not_queued'
    if control.pending_count != 1 or control.active_count != 0:
        return None, 'competing_work_prevents_start_range'
    if (workers is None or workers.state != 'observed' or not workers.ready_workers
            or not 0 <= (now - workers.as_of).total_seconds() <= 3):
        return None, 'worker_readiness_not_observed'
    active_caller = session.scalar(select(InterestRecord.id).join(CollectionRecord,
        CollectionRecord.id == InterestRecord.collection_id).where(
            InterestRecord.acquisition_id == record.id, InterestRecord.status == 'queued',
            CollectionRecord.status == 'active', CollectionRecord.spec['max_duration_seconds'].as_string().is_(None),
        ).limit(1))
    if active_caller is None:
        return None, 'request_eligibility_not_verified'
    policy_id, version, _, _, _ = constraint
    started = AcquisitionRecord.outcome['attempts'][0]['started_at'].as_string()
    requirements = select(AcquisitionRecord.requirements).where(AcquisitionRecord.id == record.id).correlate(None).scalar_subquery()
    # Only one globally pending acquisition is eligible for this initial estimator.
    # Read at most twenty comparable public starts, without loading evidence payloads.
    rows = session.execute(select(AcquisitionRecord.created_at, started).where(
        AcquisitionRecord.domain == record.domain,
        AcquisitionRecord.status == 'succeeded', AcquisitionRecord.attempt_count == 1,
        AcquisitionRecord.dispatch_policy_version == control.policy_version,
        AcquisitionRecord.requirements == requirements,
        AcquisitionRecord.attempt_domain_policy['id'].as_string() == str(policy_id),
        AcquisitionRecord.attempt_domain_policy['version'].as_integer() == version,
        AcquisitionRecord.completed_at >= now - timedelta(seconds=600),
        AcquisitionRecord.completed_at <= now, func.length(started) <= 64,
    ).order_by(AcquisitionRecord.completed_at.desc(), AcquisitionRecord.id).limit(20)).all()
    waits = []
    newest = None
    for admitted, value in rows:
        try:
            start = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            continue
        if start.utcoffset() is None or not now - timedelta(seconds=600) <= start <= now:
            continue
        admitted = admitted.replace(tzinfo=UTC) if admitted.tzinfo is None else admitted
        seconds = (start - admitted).total_seconds()
        if 0 <= seconds <= 600:
            waits.append(seconds)
            newest = max(newest, start) if newest is not None else start
    if len(waits) < 3:
        return None, 'insufficient_comparable_starts'
    if newest < now - timedelta(seconds=120):
        return None, 'comparable_starts_are_stale'
    admitted_at = record.created_at.replace(tzinfo=UTC) if record.created_at.tzinfo is None else record.created_at
    estimate = observed_range(waits, admitted_at=admitted_at, eligible_at=eligible_at, now=now)
    return estimate, None if estimate else 'current_wait_outside_observed_range'
