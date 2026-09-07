"""Typed HTTP contracts for collections and crawler controls.

The server owns URL normalization, SQL validation, admission, and execution.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")



class CollectionSpec(Snapshot):
    model_config = ConfigDict(extra="forbid", frozen=True)
    seed_urls: tuple[str, ...] = Field(default=(), max_length=1000)
    seed_description: str | None = Field(default=None, min_length=1, max_length=4000)
    seed_sql: str | None = Field(default=None, max_length=20000)
    seed_parameters: tuple[JsonValue, ...] = Field(default=(), max_length=100)
    follow_sql: str = Field(
        default="SELECT target_url AS url FROM nav.links", max_length=20000
    )
    max_depth: int = Field(default=0, ge=0, le=100)
    page_limit: int = Field(default=25, ge=1, le=100000)
    result_max_age_seconds: int = Field(default=300, ge=0, le=3600)
    visibility: Literal["public", "private"] = "public"
    access_context: str = Field(default="public", min_length=1, max_length=200)
    allowed_sections: tuple[str, ...] = Field(default=(), max_length=100)
    deadline_at: datetime | None = None


class AdmissionEstimate(Snapshot):
    earliest_at: datetime
    latest_at: datetime
    calculated_at: datetime
    expires_at: datetime
    sample_size: int = Field(ge=3, le=20)
    sample_window_seconds: Literal[600] = 600
    basis: Literal["recent_single_url_submission_to_admission_waits"]
    uncertainty: Literal["conditional_not_a_guarantee"]
    assumptions: Literal["unchanged_policies_worker_readiness_and_competing_work"]
    scope: Literal["first_admission"]


class AdmissionWait(Snapshot):
    pending_candidates: int = 0
    preview_urls: tuple[str, ...] = ()
    oldest_selected_at: datetime | None = None
    elapsed_seconds: float | None = None
    estimate: AdmissionEstimate | None = None
    estimate_unavailable_reason: str | None


class QueueConstraint(Snapshot):
    reason: str
    pages: int


class CollectionQueue(Snapshot):
    runnable_pages: int
    deferred_pages: int
    unknown_pages: int
    oldest_admitted_at: datetime | None
    oldest_wait_seconds: float | None
    constraints: tuple[QueueConstraint, ...]
    basis: Literal["stored_eligibility_permits_rechecked_at_start"]


class CurrentCollection(Snapshot):
    source: Literal["current"] = "current"
    id: UUID
    specification: CollectionSpec
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
    acquiring_pages: int = 0
    selecting_pages: int = 0
    supplied_pages: int = 0
    failed_pages: int = 0
    shared_pages: int = 0
    reused_pages: int = 0
    ingested_pages: int = 0
    lineage_ready: bool = False
    admission: AdmissionWait
    queue: CollectionQueue
    last_progress_at: datetime | None
    discovery_stage: str | None = None
    search_queries: tuple[str, ...] = ()
    resolved_urls: tuple[str, ...] = ()
    query_ready: bool | None = None
    query_readiness_reason: str = "catalogue_commit_not_verified"
    query_readiness_as_of: datetime | None = None
    query_generation_id: UUID | None = None
    as_of: datetime


class HistoricalCollection(Snapshot):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["history"] = "history"
    id: UUID
    specification: CollectionSpec
    created_at: datetime
    completed_at: datetime | None
    outcome: str | None
    consumed_pages: int | None = Field(ge=0)
    supplied_pages: int | None = Field(ge=0)
    failed_pages: int | None = Field(ge=0)
    seed_provenance: dict | None
    as_of: datetime
    query_ready: bool | None = None
    query_readiness_reason: str = "materialization_commit_not_verified"
    query_readiness_as_of: datetime | None = None
    query_generation_id: UUID | None = None


class HistoricalCollectionSummary(Snapshot):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    visibility: Literal["public", "private"]
    summary: str = Field(max_length=500)
    created_at: datetime
    completed_at: datetime | None
    outcome: str | None
    consumed_pages: int | None = Field(ge=0)
    supplied_pages: int | None = Field(ge=0)
    failed_pages: int | None = Field(ge=0)


class CollectionHistoryPage(Snapshot):
    source: Literal["history"] = "history"
    items: list[HistoricalCollectionSummary]
    next_cursor: str | None
    as_of: datetime


class UrlExclusion(Snapshot):
    model_config = ConfigDict(extra="forbid", frozen=True)
    host: str = Field(min_length=1, max_length=253)
    path_prefix: str = Field(default="/", min_length=1, max_length=2048)


class FrontierSettings(Snapshot):
    model_config = ConfigDict(extra="forbid", frozen=True)
    paused: bool = False
    exclusions: tuple[UrlExclusion, ...] = Field(default=(), max_length=100)
    collection_limit: int = Field(default=1000, ge=1, le=100000)
    interest_limit: int = Field(default=200000, ge=1, le=10000000)
    acquisition_limit: int = Field(default=10000, ge=1, le=1000000)
    admission_limit: int = Field(default=10000, ge=1, le=1000000)
    dispatch_limit: int = Field(default=48, ge=1, le=10000)
    captures_per_minute: int | None = Field(default=60, ge=1, le=60000)
    background_share: int = Field(default=0, ge=0, le=99)
    background_attempt_allowance: int = Field(default=1000, ge=0, le=1000000000)
    background_capture_time_allowance_ms: int = Field(default=12500000, ge=0, le=1000000000000)
    attempt_allowance: int = Field(default=10000, ge=0, le=1000000000)
    capture_time_allowance_ms: int = Field(default=86400000, ge=0, le=1000000000000)
    capture_timeout_ms: int = Field(default=120000, ge=1000, le=3600000)


class FrontierControlView(Snapshot):
    settings: FrontierSettings
    policy_version: int
    updated_at: datetime | None
    updated_by: str | None
    retained_acquisitions: int
    acquisition_admission_waiting_reason: str | None
    pending_acquisitions: int
    dispatched_acquisitions: int
    retained_interests: int
    reserved_attempts: int
    started_attempts: int
    reserved_capture_ms: int
    charged_capture_ms: int
    background_reserved_attempts: int
    background_started_attempts: int
    background_reserved_capture_ms: int
    background_charged_capture_ms: int
    background_waiting_reason: str | None
    background_share_semantics: Literal["percent_when_both_eligible_spare_capacity_otherwise"] = "percent_when_both_eligible_spare_capacity_otherwise"
    dispatch_waiting_reason: str | None
    allowance_semantics: Literal["cumulative_until_operator_increases_limit"] = "cumulative_until_operator_increases_limit"
    time_semantics: Literal["client_capture_elapsed_not_provider_billing"] = "client_capture_elapsed_not_provider_billing"
    next_rate_eligibility_at: datetime | None
    pause_behavior: Literal["finish_started_captures"] = "finish_started_captures"
    rate_semantics: Literal["dispatch_upper_bound_null_is_unlimited"] = "dispatch_upper_bound_null_is_unlimited"
    as_of: datetime


class DomainPolicySnapshot(Snapshot):
    model_config = ConfigDict(frozen=True)
    id: UUID
    paused: bool = False
    version: int = Field(default=1, ge=1)
    updated_by: str = "setup"
    slug: str
    host_match: str
    maximum_concurrency: int = Field(ge=1, le=10_000)
    minimum_request_interval_seconds: float = Field(ge=0, le=3600)


class DomainPolicyRecord(DomainPolicySnapshot):
    enabled: bool
    created_at: datetime
    updated_at: datetime


class DomainPolicyListResponse(Snapshot):
    items: list[DomainPolicyRecord]
    total: int
    limit: int
    offset: int


class DomainPolicyCreateRequest(Snapshot):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    host_match: str
    maximum_concurrency: int = Field(default=4, ge=1, le=10_000)
    minimum_request_interval_seconds: float = Field(default=0, ge=0, le=3600)
    enabled: bool = True
    paused: bool = False


class DomainPolicyUpdateRequest(Snapshot):
    expected_version: int = Field(ge=1)
    paused: bool | None = None
    host_match: str | None = None
    maximum_concurrency: int | None = Field(default=None, ge=1, le=10_000)
    minimum_request_interval_seconds: float | None = Field(default=None, ge=0, le=3600)
    enabled: bool | None = None


# Read-only current work and durable evidence contracts.


class SelectionContext(Snapshot):
    model_config = ConfigDict(extra="forbid", frozen=True)

    depth: int = Field(ge=0)
    parent_observation_id: UUID | None = None
    rule_id: str = Field(min_length=1, max_length=200)


class Caller(Snapshot):
    collection_id: UUID
    interest_id: UUID
    status: str
    mode: str


class StartEstimate(Snapshot):
    earliest_at: datetime
    latest_at: datetime
    calculated_at: datetime
    expires_at: datetime
    sample_size: int = Field(ge=3, le=20)
    sample_window_seconds: Literal[600] = 600
    basis: Literal['recent_comparable_observed_wait_range'] = 'recent_comparable_observed_wait_range'
    uncertainty: Literal['conditional_not_a_guarantee'] = 'conditional_not_a_guarantee'
    assumptions: Literal['unchanged_policies_worker_readiness_and_competing_work'] = 'unchanged_policies_worker_readiness_and_competing_work'


class AcquisitionView(Snapshot):
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


class CollectionItem(Snapshot):
    interest_id: UUID
    status: str
    mode: str
    budget_state: str
    context: SelectionContext
    admitted_at: datetime
    acquisition: AcquisitionView


class CollectionItemsPage(Snapshot):
    source: Literal["current"] = "current"
    collection_id: UUID
    items: list[CollectionItem]
    next_after: UUID | None
    as_of: datetime
    ordering: Literal["interest_identity_not_dispatch_order"] = "interest_identity_not_dispatch_order"


class CollectionArrival(Snapshot):
    fulfillment_id: UUID
    observation_id: UUID
    requested_url: str = Field(max_length=8192)
    parent_observation_id: UUID | None
    depth: int = Field(ge=0)
    rule_id: str = Field(max_length=200)
    mode: Literal["acquired", "shared", "reused"]
    decided_at: datetime
    observation_committed: bool
    effective_url: str | None = Field(max_length=8192)
    observed_at: datetime | None
    outcome: str | None
    http_status_code: int | None
    query_ready: bool | None = None
    query_readiness_reason: str = "materialization_commit_not_verified"


class CollectionArrivalsPage(Snapshot):
    source: Literal["history"] = "history"
    collection_id: UUID
    definition_committed: bool = True
    items: list[CollectionArrival]
    next_cursor: str | None
    as_of: datetime


class RecentCapture(Snapshot):
    observation_id: UUID
    requested_url: str = Field(max_length=8192)
    completed_at: datetime
    evidence_committed: bool
    query_ready: bool | None = None
    query_readiness_reason: str = "materialization_commit_not_verified"


class DomainActivity(Snapshot):
    request_queued_urls: int | None = None
    unique_queued_urls: int
    domain: str
    queued: int
    dispatched: int
    started: int
    oldest_wait_at: datetime | None


class UpcomingItem(Snapshot):
    acquisition_id: UUID
    requested_url: str
    domain: str
    admitted_at: datetime
    retry_not_before: datetime


class ActiveItem(Snapshot):
    acquisition_id: UUID
    requested_url: str
    attempt_started_at: datetime | None


class CurrentActivity(Snapshot):
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
    upcoming_semantics: Literal["oldest_pending_preview_not_dispatch_order"] = "oldest_pending_preview_not_dispatch_order"
    next_start_estimate: StartEstimate | None = None
    estimate_unavailable_reason: str | None = "domain_permits_and_dispatch_capacity_not_observed"
    recent: list[RecentCapture]


class VelocityWindow(Snapshot):
    seconds: Literal[60, 300]
    domain: str | None
    attempt_starts: int
    successful_captures: int
    failed_captures: int
    fulfillments: int
    attempt_starts_per_minute: float


class HistoricalActivity(Snapshot):
    as_of: datetime
    window_end: datetime
    completeness: Literal["committed_evidence_only_ingestion_may_lag"] = "committed_evidence_only_ingestion_may_lag"
    domain_preview_limit: Literal[10] = 10
    velocities: list[VelocityWindow]
    recent: list[RecentCapture]


class CrawlerActivity(Snapshot):
    as_of: datetime
    state: Literal["observed", "no_recent_reports", "unavailable"]
    reason: str | None = None
    reported_workers: int | None = None
    ready_workers: int | None = None
    blocked_workers: int | None = None
    checking_workers: int | None = None
    unknown_workers: int | None = None
    waiting_reasons: list[Literal["ingestion_delivery_unavailable", "storage_unavailable", "cdp_unavailable"]] = []
    excluded_reports: int | None = None
    more_workers: bool = False


class LiveView(Snapshot):
    workers: CrawlerActivity
    current: CurrentActivity
    history: HistoricalActivity | None
    history_unavailable_reason: str | None
    recent: list[RecentCapture]
    visibility: Literal["public"] = "public"


class ObservationLineageItem(Snapshot):
    record_id: UUID
    kind: Literal['fulfillment', 'reason']
    decided_at: datetime
    collection_id: UUID | None
    parent_observation_id: UUID | None
    rule_id: str = Field(max_length=200)
    depth: int | None = Field(ge=0)
    mode: Literal['acquired', 'shared', 'reused'] | None
    reason: Literal['collection', 'background'] | None
    policy_version: str | None = Field(max_length=200)


class ObservationLineagePage(Snapshot):
    observation_id: UUID
    requested_url: str = Field(max_length=8192)
    items: list[ObservationLineageItem]
    next_cursor: str | None
    as_of: datetime
    completeness: Literal['committed_visible_evidence_only_ingestion_may_lag'] = 'committed_visible_evidence_only_ingestion_may_lag'


class CapturePage(Snapshot):
    items: list[RecentCapture]
    cursor: str
    has_more: bool
    bootstrap: bool
    reset_reason: Literal["cursor_expired", "clock_moved_backwards"] | None
    as_of: datetime
    source: Literal["retained_public_completions"]
