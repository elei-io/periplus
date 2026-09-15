"""Bounded public crawler visibility; current work and committed evidence stay distinct."""

from datetime import UTC, datetime
from threading import Timer
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import case, func, literal, select

from periplus.crawl.runtime.frontier_models import (
    AcquisitionRecord,
    FrontierControlRecord,
    InterestRecord,
)
from periplus.crawl.control.collections.models import CollectionRecord


class RecentCapture(BaseModel):
    observation_id: UUID
    requested_url: str = Field(max_length=8192)
    completed_at: datetime
    evidence_committed: bool
    query_ready: bool | None = None
    query_readiness_reason: str = "materialization_commit_not_verified"


class DomainActivity(BaseModel):
    domain: str
    queued: int
    dispatched: int
    started: int
    oldest_wait_at: datetime | None
    unique_queued_urls: int
    request_queued_urls: int | None = None


class UpcomingItem(BaseModel):
    acquisition_id: UUID
    requested_url: str
    domain: str
    admitted_at: datetime
    retry_not_before: datetime


from periplus.crawl.runtime.start_estimates import StartEstimate


class ActiveItem(BaseModel):
    acquisition_id: UUID
    requested_url: str
    attempt_started_at: datetime | None


class CurrentActivity(BaseModel):
    as_of: datetime
    paused: bool
    queued: int
    dispatched: int
    started: int
    oldest_wait_at: datetime | None
    domains: list[DomainActivity]
    more_domains: bool
    upcoming: list[UpcomingItem]
    active: list[ActiveItem] = Field(default_factory=list)
    more_active: bool = False
    upcoming_semantics: Literal["oldest_pending_preview_not_dispatch_order"] = (
        "oldest_pending_preview_not_dispatch_order"
    )
    next_start_estimate: StartEstimate | None = None
    estimate_unavailable_reason: str | None = (
        "domain_permits_and_dispatch_capacity_not_observed"
    )
    recent: list[RecentCapture]


class VelocityWindow(BaseModel):
    seconds: Literal[60, 300]
    domain: str | None
    attempt_starts: int
    successful_captures: int
    failed_captures: int
    fulfillments: int
    attempt_starts_per_minute: float


class HistoricalActivity(BaseModel):
    as_of: datetime
    window_end: datetime
    completeness: Literal["current_operational_window"] = (
        "current_operational_window"
    )
    domain_preview_limit: Literal[10] = 10
    velocities: list[VelocityWindow]
    recent: list[RecentCapture]


from periplus.crawl.runtime.frontier_health import CrawlerActivity


class LiveView(BaseModel):
    workers: CrawlerActivity
    current: CurrentActivity
    history: HistoricalActivity | None
    history_unavailable_reason: str | None
    recent: list[RecentCapture]


def _aware(value: datetime | None):
    return (
        value.replace(tzinfo=UTC)
        if value is not None and value.tzinfo is None
        else value
    )


def current_activity(sessions, *, collection_id: UUID | None = None) -> CurrentActivity:
    with sessions() as session:
        control = session.scalar(
            select(FrontierControlRecord)
            .where(FrontierControlRecord.id == 1)
            .with_for_update(read=True)
        )
        now = datetime.now(UTC)
        queued = AcquisitionRecord.status.in_(("queued", "retry"))
        dispatched = AcquisitionRecord.status == "dispatched"
        active = dispatched & AcquisitionRecord.attempt_started_at.is_not(None)
        request_member = (
            select(InterestRecord.id)
            .join(CollectionRecord, CollectionRecord.id == InterestRecord.collection_id)
            .where(
                InterestRecord.acquisition_id == AcquisitionRecord.id,
                InterestRecord.collection_id == collection_id,
                InterestRecord.status.in_(("queued", "awaiting_result")),
                CollectionRecord.retiring.is_(False),
            )
            .exists()
        )
        request_count = (
            func.count(
                func.distinct(case((queued & request_member, AcquisitionRecord.url)))
            )
            if collection_id is not None
            else literal(None)
        )
        counters = (
            func.sum(case((queued, 1), else_=0)),
            func.sum(case((dispatched, 1), else_=0)),
            func.sum(case((active, 1), else_=0)),
            func.min(case((queued, AcquisitionRecord.created_at))),
            func.count(func.distinct(case((queued, AcquisitionRecord.url)))),
            request_count,
        )
        visible = (AcquisitionRecord.status.in_(("queued", "retry", "dispatched")),)
        totals = session.execute(select(*counters).where(*visible)).one()
        domains = session.execute(
            select(AcquisitionRecord.domain, *counters)
            .where(*visible)
            .group_by(AcquisitionRecord.domain)
            .order_by(
                counters[2].desc(),
                counters[1].desc(),
                counters[0].desc(),
                AcquisitionRecord.domain,
            )
            .limit(11)
        ).all()
        upcoming = session.execute(
            select(
                AcquisitionRecord.id,
                AcquisitionRecord.url,
                AcquisitionRecord.domain,
                AcquisitionRecord.created_at,
                AcquisitionRecord.eligible_at,
            )
            .where(queued)
            .order_by(AcquisitionRecord.created_at, AcquisitionRecord.id)
            .limit(5)
        ).all()
        active_rows = session.execute(
            select(
                AcquisitionRecord.id,
                AcquisitionRecord.url,
                AcquisitionRecord.attempt_started_at,
            )
            .where(dispatched)
            .order_by(AcquisitionRecord.created_at, AcquisitionRecord.id)
            .limit(13)
        ).all()
        recent = session.execute(
            select(
                AcquisitionRecord.id,
                AcquisitionRecord.url,
                AcquisitionRecord.completed_at,
                AcquisitionRecord.evidence_committed_at,
            )
            .where(
                AcquisitionRecord.status == "succeeded",
                AcquisitionRecord.completed_at.is_not(None),
            )
            .order_by(
                AcquisitionRecord.completed_at.desc(), AcquisitionRecord.id.desc()
            )
            .limit(5)
        ).all()
        return CurrentActivity(
            as_of=now,
            paused=control.paused,
            queued=totals[0] or 0,
            dispatched=totals[1] or 0,
            started=totals[2] or 0,
            oldest_wait_at=_aware(totals[3]),
            domains=[
                DomainActivity(
                    domain=row[0],
                    queued=row[1],
                    dispatched=row[2],
                    started=row[3],
                    oldest_wait_at=_aware(row[4]),
                    unique_queued_urls=row[5],
                    request_queued_urls=row[6],
                )
                for row in domains[:10]
            ],
            more_domains=len(domains) > 10,
            active=[
                ActiveItem(
                    acquisition_id=row[0],
                    requested_url=row[1],
                    attempt_started_at=_aware(row[2]),
                )
                for row in active_rows[:12]
            ],
            more_active=len(active_rows) > 12,
            upcoming=[
                UpcomingItem(
                    acquisition_id=row[0],
                    requested_url=row[1],
                    domain=row[2],
                    admitted_at=_aware(row[3]),
                    retry_not_before=_aware(row[4]),
                )
                for row in upcoming
            ],
            recent=[
                RecentCapture(
                    observation_id=row[0],
                    requested_url=row[1],
                    completed_at=_aware(row[2]),
                    evidence_committed=row[3] is not None,
                )
                for row in recent
            ],
        )


def count_attempt_starts(session, *, since: datetime, until: datetime) -> int:
    """Count distinct physical starts, including retries, in retained execution."""
    from sqlalchemy import text
    return session.scalar(text("""
        WITH recent AS (
            SELECT id, attempt_started_at, prior_results, uncertain_attempts, outcome
            FROM state.frontier_acquisitions
            WHERE attempt_count > 0 AND (completed_at IS NULL OR completed_at >= :since)
        ), starts AS (
            SELECT id, attempt_started_at AS started_at FROM recent
            UNION
            SELECT id, (item->'attempt_evidence'->>'started_at')::timestamptz
            FROM recent CROSS JOIN LATERAL jsonb_array_elements(prior_results) AS item
            UNION
            SELECT id, (item->>'started_at')::timestamptz
            FROM recent CROSS JOIN LATERAL jsonb_array_elements(uncertain_attempts) AS item
            UNION
            SELECT id, (item->>'started_at')::timestamptz
            FROM recent CROSS JOIN LATERAL jsonb_array_elements(coalesce(outcome->'attempts','[]'::jsonb)) AS item
        ) SELECT count(*) FROM starts WHERE started_at >= :since AND started_at <= :until
        """), {'since': since, 'until': until})
