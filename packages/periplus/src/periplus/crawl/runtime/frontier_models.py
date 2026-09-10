"""Authoritative shared acquisition, interest, selection, and dispatch state."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from periplus.platform.postgres.base import Base
from periplus.platform.postgres.types import json_type, utc_now


class FrontierControlRecord(Base):
    """One crawler's admission/dispatch counters, locked only during short local transactions."""

    __tablename__ = "frontier_control"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_frontier_single_control"),
        CheckConstraint("capture_timeout_ms BETWEEN 1000 AND 3600000",
                        name="ck_frontier_capture_timeout"),
        CheckConstraint("pending_count >= 0 AND active_count >= 0 AND interest_count >= 0", name="ck_frontier_counts"),
        CheckConstraint("acquisition_limit > 0 AND admission_limit > 0 AND dispatch_limit > 0 AND collection_limit > 0 AND interest_limit > 0", name="ck_frontier_limits"),
        CheckConstraint("captures_per_minute >= 0", name="ck_frontier_rates"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    paused: Mapped[bool] = mapped_column(default=False)
    exclusions: Mapped[list[dict[str, Any]]] = mapped_column(json_type, default=list)
    exclusion_cursor: Mapped[UUID | None] = mapped_column()
    retention_cursor: Mapped[UUID | None] = mapped_column()
    collection_retention_cursor: Mapped[UUID | None] = mapped_column()
    collection_limit: Mapped[int] = mapped_column(default=1000)
    interest_limit: Mapped[int] = mapped_column(default=200000)
    interest_count: Mapped[int] = mapped_column(default=0)
    acquisition_limit: Mapped[int] = mapped_column(default=10000)
    admission_limit: Mapped[int] = mapped_column(default=10000)
    dispatch_limit: Mapped[int] = mapped_column(default=48)
    pending_count: Mapped[int] = mapped_column(default=0)
    active_count: Mapped[int] = mapped_column(default=0)
    captures_per_minute: Mapped[int] = mapped_column(default=60)
    next_dispatch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capture_timeout_ms: Mapped[int] = mapped_column(default=120000)
    policy_version: Mapped[int] = mapped_column(default=1)
    scheduling_turn: Mapped[int] = mapped_column(default=0)
    last_dispatch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(Text)



class AcquisitionRecord(Base):
    __tablename__ = "frontier_acquisitions"
    __table_args__ = (
        UniqueConstraint("pending_key", name="uq_frontier_pending_capture"),
        CheckConstraint("status IN ('queued', 'retry', 'dispatched', 'succeeded', 'failed', 'cancelled')",
                        name="ck_frontier_acquisition_status"),
        CheckConstraint("generation >= 0", name="ck_frontier_generation"),
        CheckConstraint("attempt_count >= 0 AND attempt_limit > 0 AND attempt_count <= attempt_limit",
                        name="ck_frontier_attempt_limit"),
        Index("ix_frontier_eligible", "status", "eligible_at", "created_at"),
        Index("ix_frontier_recent", "capture_key", "status", "completed_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    url: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text, index=True)
    capture_key: Mapped[str] = mapped_column(Text)
    pending_key: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[dict[str, Any]] = mapped_column(json_type)
    status: Mapped[str] = mapped_column(Text, default="queued")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    dispatch_policy_version: Mapped[int | None] = mapped_column()
    eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    defer_reason: Mapped[str | None] = mapped_column(Text)
    domain_eligible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    domain_policy_id: Mapped[UUID | None] = mapped_column()
    domain_policy_version: Mapped[int | None] = mapped_column()
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_domain_policy: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    attempt_exclusions: Mapped[list[dict[str, Any]]] = mapped_column(json_type, default=list)
    attempt_exclusion_version: Mapped[int] = mapped_column(default=1)
    attempt_reserved_ms: Mapped[int] = mapped_column(default=0)
    attempt_count: Mapped[int] = mapped_column(default=0)
    attempt_limit: Mapped[int] = mapped_column(default=3)
    attempt_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uncertain_attempts: Mapped[list[dict[str, Any]]] = mapped_column(json_type, default=list)
    prior_results: Mapped[list[dict[str, Any]]] = mapped_column(json_type, default=list)
    terminal_reason: Mapped[str | None] = mapped_column(Text)
    frozen_reasons: Mapped[list[dict[str, Any]]] = mapped_column(json_type, default=list)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    navigation: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    retired_navigation: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    evidence_snapshot: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InterestRecord(Base):
    __tablename__ = "frontier_interests"
    __table_args__ = (
        UniqueConstraint("collection_id", "url_key", name="uq_frontier_collection_url_key"),
        Index("ix_frontier_selection", "collection_id", "status", "created_at"),
        CheckConstraint("budget_state IN ('reserved', 'consumed', 'released')",
                        name="ck_frontier_interest_budget"),
        CheckConstraint("status IN ('queued', 'awaiting_result', 'selecting', 'settled', 'cancelled')",
                        name="ck_frontier_interest_status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    collection_id: Mapped[UUID] = mapped_column(ForeignKey("collections.id"), index=True)
    acquisition_id: Mapped[UUID] = mapped_column(ForeignKey("frontier_acquisitions.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    url_key: Mapped[str] = mapped_column(Text)
    context: Mapped[dict[str, Any]] = mapped_column(json_type)
    mode: Mapped[str] = mapped_column(Text)
    budget_state: Mapped[str] = mapped_column(Text, default="reserved")
    status: Mapped[str] = mapped_column(Text, default="queued")
    selection_checkpoint: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FrontierOutboxRecord(Base):
    __tablename__ = "frontier_outbox"
    __table_args__ = (
        CheckConstraint("publish_attempts >= 0", name="ck_frontier_publish_attempts"),
        CheckConstraint("receipt_checks >= 0", name="ck_frontier_receipt_checks"),
        Index("ix_frontier_ingestion_receipts", "kind", "committed_snapshot", "next_receipt_at", "published_at"),

        Index("ix_frontier_outbox_ready", "published_at", "not_before", "claim_expires_at"),
    )

    message_id: Mapped[str] = mapped_column(Text, primary_key=True)
    acquisition_id: Mapped[UUID | None] = mapped_column(ForeignKey("frontier_acquisitions.id"), index=True)
    collection_id: Mapped[UUID | None] = mapped_column(ForeignKey("collections.id"), index=True)
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(json_type)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    committed_snapshot: Mapped[int | None] = mapped_column(BigInteger)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_receipt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_checks: Mapped[int] = mapped_column(default=0)

    claim_token: Mapped[UUID | None] = mapped_column()
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
