"""Bounded public crawler visibility; current work and committed evidence stay distinct."""
from datetime import UTC, datetime
from threading import Timer
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from periplus.crawl.runtime.frontier_models import AcquisitionRecord, FrontierControlRecord
from periplus.platform.catalogue.connection import _identifier


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


class UpcomingItem(BaseModel):
    acquisition_id: UUID
    requested_url: str
    domain: str
    admitted_at: datetime
    retry_not_before: datetime


from periplus.crawl.runtime.start_estimates import StartEstimate


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
    upcoming_semantics: Literal["oldest_pending_preview_not_dispatch_order"] = "oldest_pending_preview_not_dispatch_order"
    next_start_estimate: StartEstimate | None = None
    estimate_unavailable_reason: str | None = "domain_permits_and_dispatch_capacity_not_observed"
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
    completeness: Literal["committed_evidence_only_ingestion_may_lag"] = "committed_evidence_only_ingestion_may_lag"
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
    visibility: Literal["public"] = "public"


def _aware(value: datetime | None):
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def current_activity(sessions) -> CurrentActivity:
    with sessions() as session:
        control = session.scalar(select(FrontierControlRecord).where(FrontierControlRecord.id == 1).with_for_update(read=True))
        now = datetime.now(UTC)
        queued = AcquisitionRecord.status.in_(("queued", "retry"))
        dispatched = AcquisitionRecord.status == "dispatched"
        active = dispatched & AcquisitionRecord.attempt_started_at.is_not(None)
        counters = (func.sum(case((queued, 1), else_=0)), func.sum(case((dispatched, 1), else_=0)),
                    func.sum(case((active, 1), else_=0)), func.min(case((queued, AcquisitionRecord.created_at))))
        visible = (AcquisitionRecord.visibility == "public", AcquisitionRecord.status.in_(("queued", "retry", "dispatched")))
        totals = session.execute(select(*counters).where(*visible)).one()
        domains = session.execute(select(AcquisitionRecord.domain, *counters).where(*visible).group_by(
            AcquisitionRecord.domain).order_by(counters[2].desc(), counters[1].desc(), counters[0].desc(),
                                               AcquisitionRecord.domain).limit(11)).all()
        upcoming = session.execute(select(AcquisitionRecord.id, AcquisitionRecord.url, AcquisitionRecord.domain,
            AcquisitionRecord.created_at, AcquisitionRecord.eligible_at).where(AcquisitionRecord.visibility == "public",
                queued).order_by(AcquisitionRecord.created_at, AcquisitionRecord.id).limit(5)).all()
        recent = session.execute(select(AcquisitionRecord.id, AcquisitionRecord.url,
            AcquisitionRecord.completed_at, AcquisitionRecord.evidence_snapshot).where(
                AcquisitionRecord.visibility == "public", AcquisitionRecord.status == "succeeded",
                AcquisitionRecord.completed_at.is_not(None)).order_by(
                    AcquisitionRecord.completed_at.desc(), AcquisitionRecord.id.desc()).limit(5)).all()
        return CurrentActivity(as_of=now, paused=control.paused, queued=totals[0] or 0,
            dispatched=totals[1] or 0, started=totals[2] or 0, oldest_wait_at=_aware(totals[3]),
            domains=[DomainActivity(domain=row[0], queued=row[1], dispatched=row[2], started=row[3],
                oldest_wait_at=_aware(row[4])) for row in domains[:10]], more_domains=len(domains) > 10,
            upcoming=[UpcomingItem(acquisition_id=row[0], requested_url=row[1], domain=row[2],
                admitted_at=_aware(row[3]), retry_not_before=_aware(row[4])) for row in upcoming],
            recent=[RecentCapture(observation_id=row[0], requested_url=row[1], completed_at=_aware(row[2]),
                                  evidence_committed=row[3] is not None) for row in recent])


def read_live_history(catalogue, *, now: datetime | None = None) -> HistoricalActivity:
    now = now or datetime.now(UTC)
    connection = catalogue.trusted_connection
    alias = _identifier(catalogue.config.alias)
    timer = Timer(10, connection.interrupt)
    timer.daemon = True
    timer.start()
    try:
        # Rates describe committed attempt starts, capture outcomes, and request
        # fulfillments separately. Imported observations are not crawler activity.
        rows = connection.execute(f"""
            WITH clock AS (SELECT ?::TIMESTAMPTZ AS as_of),
            windows(seconds) AS (VALUES (60), (300)),
            events AS (
                SELECT v.requested_url, a.started_at AS event_at, 'attempt' AS kind
                FROM {alias}.ingest.attempts a JOIN {alias}.ingest.visits v ON v.visit_id = a.visit_id, clock
                WHERE v.visibility = 'public' AND v.provenance.kind = 'periplus'
                  AND a.started_at >= as_of - INTERVAL '300 seconds' AND a.started_at <= as_of
                UNION ALL
                SELECT v.requested_url, v.finished_at, v.outcome
                FROM {alias}.ingest.visits v, clock
                WHERE v.visibility = 'public' AND v.provenance.kind = 'periplus'
                  AND v.outcome IN ('succeeded', 'failed')
                  AND v.finished_at >= as_of - INTERVAL '300 seconds' AND v.finished_at <= as_of
                UNION ALL
                SELECT f.requested_url, f.recorded_at, 'fulfillment'
                FROM {alias}.ingest.fulfillments f, clock
                WHERE f.visibility = 'public' AND f.recorded_at >= as_of - INTERVAL '300 seconds'
                  AND f.recorded_at <= as_of
            ), counted AS (
                SELECT seconds, trim(regexp_extract(requested_url, '^https?://(\\[[^\\]]+\\]|[^/:?#]+)', 1), '[]') AS domain,
                    grouping(domain) AS is_global,
                    count(*) FILTER (WHERE kind = 'attempt') AS attempts,
                    count(*) FILTER (WHERE kind = 'succeeded') AS succeeded,
                    count(*) FILTER (WHERE kind = 'failed') AS failed,
                    count(*) FILTER (WHERE kind = 'fulfillment') AS fulfilled
                FROM windows CROSS JOIN clock LEFT JOIN events
                    ON event_at >= as_of - seconds * INTERVAL '1 second'
                GROUP BY GROUPING SETS ((seconds), (seconds, domain))
            )
            SELECT seconds, domain, attempts, succeeded, failed, fulfilled
            FROM counted
            WHERE is_global = 1 OR domain IS NOT NULL
            QUALIFY domain IS NULL OR row_number() OVER (
                PARTITION BY seconds, domain IS NULL ORDER BY attempts DESC, domain) <= 10
            ORDER BY seconds, domain NULLS FIRST
        """, [now]).fetchall()
        latest = connection.execute(f"""SELECT visit_id,
            CASE WHEN length(requested_url) <= 8192 THEN requested_url ELSE NULL END, finished_at
            FROM {alias}.ingest.visits WHERE visibility = 'public' AND provenance.kind = 'periplus'
              AND outcome = 'succeeded' AND finished_at <= ?
            ORDER BY finished_at DESC, visit_id DESC LIMIT 5""", [now]).fetchall()
        from periplus.materialization.readiness import observation_readiness
        proofs = observation_readiness(catalogue, [row[0] for row in latest])
        return HistoricalActivity(as_of=datetime.now(UTC), window_end=now,
            velocities=[VelocityWindow(seconds=row[0], domain=row[1], attempt_starts=row[2],
                successful_captures=row[3], failed_captures=row[4], fulfillments=row[5],
                attempt_starts_per_minute=row[2] * 60 / row[0]) for row in rows],
            recent=[RecentCapture(observation_id=row[0], requested_url=row[1], completed_at=row[2],
                                  evidence_committed=True, query_ready=proofs[row[0]].query_ready,
                                  query_readiness_reason=proofs[row[0]].reason) for row in latest])
    finally:
        timer.cancel()
        timer.join()
